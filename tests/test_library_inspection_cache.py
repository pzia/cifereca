"""#2565: one-shot private inspection reuse, freshness, revocation and no leaks."""
import json
import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import patch

from cifereca.core.library_audit_broker import LibraryReconciliationReadBrokerImpl
from cifereca.core.library_local_inspection import LocalInspection, open_local_inspection
from cifereca.core import library_inspection_cache as cache
from tests.test_library_audit_broker import scoped
from tests.test_library_reconciliation import filesystem


def test_redacted_maintenance_audit_hands_off_one_private_inspection(scoped, tmp_path):
    service, _, _ = scoped
    before = filesystem(tmp_path)
    control_state = [False]
    control = NS(cancelled=lambda: control_state[0], report=lambda **kw: None)
    public = LibraryReconciliationReadBrokerImpl(service).audit(identity_id='alice', control=control)
    assert public['status'] == 'complete'
    assert 'PRIVATE' not in json.dumps(public)
    control_state[0] = True  # Completion of Maintenance task is not scope revocation.
    with patch('cifereca.core.library_audit_broker.read_library_snapshot',
               side_effect=AssertionError('SECOND_SQLITE_READ')):
        session = open_local_inspection(service, identity_id='alice')
    assert isinstance(session, LocalInspection)
    assert session.page().total == 1
    assert 'PRIVATE_TITLE' in session.page().cases[0].details
    assert filesystem(tmp_path) == before
    session.close()


def test_private_handoff_is_single_use(scoped):
    service, _, _ = scoped
    public = LibraryReconciliationReadBrokerImpl(service).audit(identity_id='alice')
    assert public['status'] == 'complete'
    session = open_local_inspection(service, identity_id='alice')
    assert isinstance(session, LocalInspection)
    with patch('cifereca.core.library_audit_broker.read_library_snapshot',
               side_effect=AssertionError('no cached result left')):
        result = open_local_inspection(service, identity_id='alice')
    assert result == {'schema': 1, 'read_only': True, 'acquisition_authorized': False,
                      'repair_authorized': False, 'status': 'incomplete_scan',
                      'reasons': ['reconciliation_unavailable']}
    session.close()


def test_epoch_change_and_identity_switch_never_expose_old_projection(scoped):
    service, _, _ = scoped
    LibraryReconciliationReadBrokerImpl(service).audit(identity_id='alice')
    service._account_context_epoch += 1
    with patch('cifereca.core.library_audit_broker.read_library_snapshot',
               side_effect=AssertionError('fresh audit required')):
        denied = open_local_inspection(service, identity_id='alice')
    assert denied['reasons'] == ['reconciliation_unavailable']
    assert 'PRIVATE' not in json.dumps(denied)

    LibraryReconciliationReadBrokerImpl(service).audit(identity_id='alice')
    service.current_identity = service.identities[1]
    denied = open_local_inspection(service, identity_id='alice')
    assert denied['reasons'] == ['reconciliation_identity_not_active']
    assert 'PRIVATE' not in json.dumps(denied)


def test_db_changes_after_audit_force_fresh_read_not_old_inspection(scoped):
    service, path, _ = scoped
    LibraryReconciliationReadBrokerImpl(service).audit(identity_id='alice')
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO entitlements(entitlement_id, identity_id, state, provider_id)'
                   ' VALUES (?, ?, ?, ?)', ('NEW_PRIVATE', 'alice', 'active', 'kobo'))
    with patch('cifereca.core.library_audit_broker.read_library_snapshot',
               side_effect=AssertionError('source changed')):
        result = open_local_inspection(service, identity_id='alice')
    assert result['reasons'] == ['reconciliation_unavailable']
    assert 'PRIVATE' not in json.dumps(result)


def test_cancelled_inspection_does_not_consume_private_result(scoped):
    service, _, _ = scoped
    LibraryReconciliationReadBrokerImpl(service).audit(identity_id='alice')
    denied = open_local_inspection(
        service, identity_id='alice',
        control=NS(cancelled=lambda: True, report=lambda **kw: None),
    )
    assert denied['reasons'] == ['audit_cancelled']
    session = open_local_inspection(service, identity_id='alice')
    assert isinstance(session, LocalInspection)
    session.close()


def test_new_audit_invalidates_older_cached_inspection_even_when_failing(scoped):
    service, _, _ = scoped
    LibraryReconciliationReadBrokerImpl(service).audit(identity_id='alice')
    with patch('cifereca.core.library_audit_broker.read_library_snapshot',
               side_effect=AssertionError('source unavailable')):
        bad = LibraryReconciliationReadBrokerImpl(service).audit(identity_id='alice')
        assert bad['reasons'] == ['reconciliation_unavailable']
        missing = open_local_inspection(service, identity_id='alice')
        assert missing['reasons'] == ['reconciliation_unavailable']


def test_cache_expires_without_authority_or_second_source_read(scoped, monkeypatch):
    service, _, _ = scoped
    time = [1000.]
    monkeypatch.setattr(cache, 'monotonic', lambda: time[0])
    LibraryReconciliationReadBrokerImpl(service).audit(identity_id='alice')
    time[0] += cache._TTL_SECONDS + 1
    with patch('cifereca.core.library_audit_broker.read_library_snapshot',
               side_effect=AssertionError('expired')):
        result = open_local_inspection(service, identity_id='alice')
    assert result['reasons'] == ['reconciliation_unavailable']


def test_source_stamp_rejects_symlinked_database_sidecar(scoped, tmp_path):
    service, path, _ = scoped
    target = tmp_path / 'PRIVATE-target'
    target.write_text('private')
    symlink = path.parent / (path.name + '-shm')
    symlink.symlink_to(target)
    assert cache._service_stamp(service) is None
