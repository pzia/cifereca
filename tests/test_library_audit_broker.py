"""Public capability boundary: no private data, authority or stale identity."""
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cifereca.config import Config
from cifereca.identities.models import Identity
from cifereca.plugin_api import LibraryReconciliationReadBroker
from cifereca.core.library_audit_broker import LibraryReconciliationReadBrokerImpl
from tests.test_library_reconciliation import epub, fixture_db, filesystem


@pytest.fixture
def scoped(tmp_path):
    root = tmp_path/'PRIVATE-library'
    for name in ('a', 'b'):
        (root/name).mkdir(parents=True)
        epub(root/name/'PRIVATE.epub')
    cfg = Config.__new__(Config)
    cfg.data = {'library': {'directory': str(root)}, 'identities': [
        {'id': 'alice', 'directory_name': 'a', 'label': 'PRIVATE-A'},
        {'id': 'bob', 'directory_name': 'b', 'label': 'PRIVATE-B'},
    ]}
    path = tmp_path/'PRIVATE.sqlite'
    fixture_db(path)
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO entitlements (entitlement_id,identity_id,state,provider_id) VALUES (?,?,?,?)',
                   ('PRIVATE-foreign-right', 'bob', 'active', 'paris'))
    identities = [Identity('alice', 'PRIVATE-A', directory_name='a'),
                  Identity('bob', 'PRIVATE-B', directory_name='b')]
    service = SimpleNamespace(config=cfg, identities=identities,
                              current_identity=identities[0], _account_context_epoch=0,
                              storage=SimpleNamespace(path=path),
                              library_dir=lambda: pytest.fail('mkdir-capable service path called'))
    return service, path, root


def test_redacted_offline_broker_keeps_identity_scope_without_any_write(scoped, tmp_path):
    service, _, root = scoped
    events = []
    control = SimpleNamespace(cancelled=lambda: False, report=lambda **kw: events.append(kw))
    broker = LibraryReconciliationReadBrokerImpl(service)
    assert isinstance(broker, LibraryReconciliationReadBroker)
    before = filesystem(tmp_path)
    result = broker.audit(identity_id='alice', control=control)
    assert result['status'] == 'complete'
    assert result['inventory']['files'] == 1
    assert result['reconciliation']['counts']['rights_without_verified_file'] == 0
    assert not result['repair_authorized'] and not result['acquisition_authorized']
    public = json.dumps([result, events])
    for private in ('PRIVATE', 'alice', 'bob', str(tmp_path), hashlib.sha256((root/'a'/'PRIVATE.epub').read_bytes()).hexdigest()):
        assert private not in public
    assert filesystem(tmp_path) == before


@pytest.mark.parametrize('identity', ['bob', 'unknown', '', None])
def test_nonactive_or_unknown_identity_is_rejected_before_source_open(scoped, identity):
    service, _, _ = scoped
    with patch('cifereca.core.library_audit_broker.read_library_snapshot', side_effect=AssertionError('source read')):
        result = LibraryReconciliationReadBrokerImpl(service).audit(identity_id=identity)
    assert result['reasons'] == ['reconciliation_identity_not_active']
    assert 'inventory' not in result


@pytest.mark.parametrize('mutation', ['epoch', 'switch-back', 'root', 'identity-directory', 'identity-list', 'config-object'])
def test_context_changes_during_inventory_discard_even_redacted_counts(scoped, mutation):
    service, _, _ = scoped
    def progress(**event):
        if event['message'] != 'Inventaire local' or event['current'] != 1:
            return
        if mutation in ('epoch', 'switch-back'):
            service._account_context_epoch += 2
        elif mutation == 'root':
            service.config.data['library']['directory'] += '-changed'
        elif mutation == 'identity-directory':
            service.config.data['identities'][0]['directory_name'] = 'b'
        elif mutation == 'identity-list':
            service.identities = list(service.identities)
        else:
            service.config = SimpleNamespace()
    control = SimpleNamespace(cancelled=lambda: False, report=progress)
    result = LibraryReconciliationReadBrokerImpl(service).audit(identity_id='alice', control=control)
    assert result['reasons'] == ['reconciliation_scope_changed']
    assert 'inventory' not in result


def test_pending_migration_is_rejected_before_library_files_are_opened(scoped, tmp_path):
    service, path, _ = scoped
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT)')
        db.execute('INSERT INTO metadata VALUES (?,?)', ('identity-directory-migration-v1', 'PRIVATE-journal'))
    before = filesystem(tmp_path)
    with patch('cifereca.core.library_audit_broker.audit_library', side_effect=AssertionError('library scan')):
        result = LibraryReconciliationReadBrokerImpl(service).audit(identity_id='alice')
    assert result['reasons'] == ['snapshot_identity_migration']
    assert filesystem(tmp_path) == before
    assert 'PRIVATE' not in json.dumps(result)


def test_callback_exception_never_becomes_public_prose(scoped):
    service, _, _ = scoped
    def fail(**kw):
        raise RuntimeError('PRIVATE path/account/title')
    result = LibraryReconciliationReadBrokerImpl(service).audit(
        identity_id='alice', control=SimpleNamespace(cancelled=lambda: False, report=fail))
    assert result['reasons'] == ['reconciliation_unavailable']
    assert 'PRIVATE' not in json.dumps(result)


def test_cancelled_task_never_opens_source(scoped):
    service, _, _ = scoped
    control = SimpleNamespace(cancelled=lambda: True, report=lambda **kw: None)
    with patch('cifereca.core.library_audit_broker.read_library_snapshot', side_effect=AssertionError('source read')):
        result = LibraryReconciliationReadBrokerImpl(service).audit(identity_id='alice', control=control)
    assert result['reasons'] == ['audit_cancelled']


def test_stale_service_projection_is_rejected_before_source_read(scoped):
    service, _, _ = scoped
    service.config.data['identities'][0]['directory_name'] = 'different'
    with patch('cifereca.core.library_audit_broker.read_library_snapshot', side_effect=AssertionError('source read')):
        result = LibraryReconciliationReadBrokerImpl(service).audit(identity_id='alice')
    assert result['reasons'] == ['reconciliation_scope_changed']


def test_cancellation_from_final_progress_is_still_visible(scoped):
    service, _, _ = scoped
    stop = [False]
    def progress(**event):
        if event['message'] == 'Audit local terminé':
            stop[0] = True
    control = SimpleNamespace(cancelled=lambda: stop[0], report=progress)
    result = LibraryReconciliationReadBrokerImpl(service).audit(identity_id='alice', control=control)
    assert result['reasons'] == ['audit_cancelled']


def test_live_wal_is_supported_through_public_boundary(scoped, tmp_path):
    service, path, _ = scoped
    db = sqlite3.connect(path)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute("INSERT INTO artifacts VALUES ('PRIVATE','hash',1,'epub')")
        db.commit()
        before = filesystem(tmp_path)
        result = LibraryReconciliationReadBrokerImpl(service).audit(identity_id='alice')
        assert result['status'] == 'complete'
        assert filesystem(tmp_path) == before
    finally:
        db.close()


def test_registered_broker_is_additive_to_legacy_library_read(scoped):
    from cifereca.brokers import build_public_brokers
    service, _, _ = scoped
    brokers = build_public_brokers(service)
    assert 'library.read' in brokers
    assert isinstance(brokers['library.reconciliation.read'], LibraryReconciliationReadBroker)
