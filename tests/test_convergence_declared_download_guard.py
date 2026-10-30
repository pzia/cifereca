"""#3171: user associations prevent duplicate acquisition without fake receipts."""
from types import SimpleNamespace

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core import library_declared_links as declarations
from cifereca.core.library_declared_links import declare_local_link, has_active_declared_link
from cifereca.core.library_inventory import scan_identity_roots
from cifereca.core.numilog_materialization_startup import execute
from cifereca.providers import ProviderConfigurationError
from tests.test_core_p0_numilog_auto_materialization import _authorize
from tests.test_core_p0_1905_numilog_offline_preview import _owned
from tests.test_core_p0_1862_numilog_store_mirror import _connect, _row
from tests.test_library_reconciliation import epub
from tests.test_sync_state_contract import _service


@pytest.fixture
def rig(tmp_path, monkeypatch):
    service = _service(tmp_path)
    account, session, calls = _connect(service, number=0)
    item = _row(account.credential_id, 0)
    _owned(service, account, lid='lid0', variant=item.record_id.rsplit(':', 1)[1])
    root = tmp_path / 'EBooks/hugues'
    book = root / 'numilog/acsm-existing/book.epub'
    book.parent.mkdir(parents=True)
    epub(book, title='Existing book')
    observation, = scan_identity_roots({'default': (root,)}).files
    scope = dict(identity_id=account.identity_id, provider_id='numilog',
                 credential_id=account.credential_id, account_id=account.unique_id,
                 item_id=item.record_id)
    options = dict(observation=observation, **scope)
    monkeypatch.setattr(declarations, 'acquisition_lock_file', lambda: tmp_path / 'acquire.lock')
    broker = SynchronizationBrokerImpl(service)
    try:
        yield service, account, session, calls, item, book, scope, options, broker
    finally:
        service.close()


def declare(rig):
    service, *_, options, broker = rig
    preview = declare_local_link(service, **options)
    return declare_local_link(service, **options, confirmation=preview.plan_token)


def revoke(rig, relation_id):
    service, *_, options, broker = rig
    preview = declare_local_link(service, **options, revoke=relation_id)
    return declare_local_link(service, **options, revoke=relation_id,
                              confirmation=preview.plan_token)


def snapshot(storage):
    with storage._lock:
        db = storage._connect()
        return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY 1').fetchall()
                for table in ('entitlements', 'acquisition_ledger', 'placements',
                              'artifacts', 'backup_items', 'catalog_entries', 'metadata')}


@pytest.mark.parametrize('file_state', ['present', 'missing', 'changed'])
def test_recorded_declaration_never_becomes_a_missing_candidate_or_receipt(rig, monkeypatch, file_state):
    service, account, session, calls, item, book, scope, options, broker = rig
    result = declare(rig)
    if file_state == 'missing':
        book.unlink()
    elif file_state == 'changed':
        book.write_bytes(b'changed')
    before = snapshot(service.storage)
    original = book.read_bytes() if book.exists() else None
    monkeypatch.setattr(declarations, 'revalidate_observed_file',
                        lambda *_a, **_k: pytest.fail('passive preflight must not hash files'))
    for selected in ('default', 'alex'):
        service.current_identity = service.identity_by_id(selected)
        check = broker.preflight_numilog_purchase(broker._source_id(account), item.record_id)
        assert not check.ok and check.facts['status'] == 'declared_link_recorded'
        assert check.facts['candidate_unverified'] is False
        assert check.facts['acquisition_authorized'] is False
        assert check.changed == 0
    assert has_active_declared_link(service.storage, **scope)
    assert result.trust == 'user_declaration'
    assert snapshot(service.storage) == before
    assert (book.read_bytes() if book.exists() else None) == original
    assert calls == []


@pytest.mark.parametrize('field,value', [
    ('identity_id', 'alex'), ('provider_id', 'kobo'),
    ('credential_id', 'another-credential'), ('account_id', 'another-account'),
    ('item_id', 'another-edition'),
])
def test_guard_matches_all_source_coordinates_only(rig, field, value):
    service, _, _, _, _, _, scope, _, _ = rig
    declare(rig)
    assert has_active_declared_link(service.storage, **scope)
    assert not has_active_declared_link(service.storage, **{**scope, field: value})


def test_revoked_association_does_not_block_a_future_independently_admitted_candidate(rig):
    service, account, _, calls, item, _, scope, _, broker = rig
    record = declare(rig)
    revoke(rig, record.relation_id)
    assert not has_active_declared_link(service.storage, **scope)
    check = broker.preflight_numilog_purchase(broker._source_id(account), item.record_id)
    assert check.ok and check.facts['status'] == 'candidate_unverified'
    assert check.facts['acquisition_authorized'] is False
    assert calls == []


def test_declaration_arriving_after_preflight_is_checked_inside_acquisition_lock(rig, monkeypatch):
    service, account, session, calls, item, book, _, _, broker = rig
    assert broker.preflight_numilog_purchase(broker._source_id(account), item.record_id).ok
    declare(rig)
    before = snapshot(service.storage)
    original = book.read_bytes()
    monkeypatch.setattr(service, 'library_identity_provider_dir',
                        lambda *_a: pytest.fail('guard must precede directory creation'))
    session.download_direct = lambda *_a: pytest.fail('declared file must not be downloaded')
    with pytest.raises(ProviderConfigurationError, match='association locale déclarée'):
        service.acquire_direct_item(item)
    assert snapshot(service.storage) == before
    assert book.read_bytes() == original and calls == []


def test_corrupt_declaration_store_cannot_be_ignored_to_download(rig):
    service, account, session, calls, item, _, _, _, broker = rig
    service.storage.metadata_set(declarations._PREFIX + '0' * 32, '{broken')
    result = broker.preflight_numilog_purchase(broker._source_id(account), item.record_id)
    assert not result.ok and result.facts['status'] == 'evidence_unavailable'
    with pytest.raises(declarations.DeclarationRejected):
        service.acquire_direct_item(item)
    assert calls == [] and service.storage.acquisitions(provider_id='numilog') == []


def test_rebind_during_declaration_read_discards_preflight(rig, monkeypatch):
    service, account, _, _, item, _, _, _, broker = rig
    original = declarations.has_active_declared_link
    def read_then_rebind(*args, **kwargs):
        result = original(*args, **kwargs)
        service.storage.provider_connection_put({
            'identity_id': 'alex', 'provider_id': 'numilog',
            'credential_id': account.credential_id,
        })
        return result
    monkeypatch.setattr(declarations, 'has_active_declared_link', read_then_rebind)
    result = broker.preflight_numilog_purchase(broker._source_id(account), item.record_id)
    assert not result.ok and result.facts['status'] == 'source_changed'


def test_unverified_declared_file_cannot_veto_consenting_download(rig):
    service, account, session, calls, item, book, _, _, broker = rig
    declare(rig)
    book.unlink()
    _authorize(broker, broker._source_id(account))
    session.library = lambda: [item]
    session.download_direct = lambda *_a: (_ for _ in ()).throw(
        RuntimeError('synthetic provider delivery unavailable')
    )
    result = execute(broker, account.identity_id, 'startup')
    assert not result.ok and result.facts['status'] == 'failed'
    assert result.facts['blocked'] == 0
    assert result.facts['failed'] == result.facts['attempted'] == 1
    assert result.changed == result.facts['downloaded'] == 0
    assert result.facts['source_results'][0]['status'] == 'failed'
    assert service.storage.acquisitions(provider_id='numilog') == []
    assert service.storage.placements() == []
    assert not book.exists() and calls == []


@pytest.mark.parametrize('changed', [True, False])
def test_declared_pair_does_not_starve_another_purchase(rig, monkeypatch, changed):
    service, account, session, calls, item, book, _, _, broker = rig
    declare(rig)
    if changed:
        book.write_bytes(b'changed since declaration')
    _authorize(broker, broker._source_id(account))
    missing = _row(account.credential_id, 1)
    session.library = lambda: [item, missing]
    acquired, indexed = [], []
    def acquire(row, *, _admission):
        assert row is missing and _admission() is missing
        acquired.append(row.record_id)
        return SimpleNamespace(metadata={})
    monkeypatch.setattr(service, 'acquire_direct_item', acquire)
    monkeypatch.setattr('cifereca.core.numilog_materialization_startup.index_delivered',
                        lambda *_a, **_k: indexed.append(True))
    result = execute(broker, account.identity_id, 'startup')
    assert result.ok is (not changed)
    assert result.facts['status'] == ('partial' if changed else 'complete')
    assert result.facts['blocked'] == 0
    assert result.facts['failed'] == int(changed)
    assert result.facts['already_local_declared'] == int(not changed)
    assert result.facts['declared_needs_review'] == int(changed)
    durable = service.storage.numilog_materialization_run_status(broker._source_id(account))
    assert durable['already_local_declared'] == int(not changed)
    assert durable['declared_needs_review'] == int(changed)
    assert result.changed == 1
    assert result.facts['attempted'] == (2 if changed else 1)
    assert result.facts['source_results'][0]['status'] == ('partial' if changed else 'complete')
    assert acquired == [missing.record_id] and indexed == [True]
    assert book.is_file() and calls == []


def test_fresh_declared_file_counts_as_local_without_receipt_or_download(rig, monkeypatch):
    from cifereca.core import numilog_materialization_startup as startup
    service, account, session, calls, item, book, _, _, broker = rig
    declare(rig)
    _authorize(broker, broker._source_id(account))
    session.library = lambda: [item]
    original = book.read_bytes()
    result = execute(broker, account.identity_id, 'startup')
    assert result.ok and result.facts['status'] == 'complete'
    assert result.facts['already_local_declared'] == result.facts['skipped'] == 1
    assert result.facts['declared_needs_review'] == 0
    durable = service.storage.numilog_materialization_run_status(broker._source_id(account))
    assert durable['already_local_declared'] == 1
    assert durable['declared_needs_review'] == 0
    assert result.changed == result.facts['attempted'] == result.facts['blocked'] == 0
    assert book.read_bytes() == original and calls == []
    assert service.storage.acquisitions(provider_id='numilog') == []
    assert service.storage.placements() == []
    later = startup.time() + 3601
    monkeypatch.setattr(startup, 'time', lambda: later)
    again = execute(broker, account.identity_id, 'startup')
    assert again.ok and again.changed == again.facts['attempted'] == 0
    assert again.facts['already_local_declared'] == 1
    assert book.read_bytes() == original
    assert service.storage.acquisitions(provider_id='numilog') == []
    assert service.storage.placements() == []


def test_declared_checks_share_one_source_time_budget(rig, monkeypatch):
    from cifereca.core import library_declared_presence as presence
    from cifereca.core import numilog_materialization_startup as startup
    service, account, session, calls, item, book, scope, _, broker = rig
    declare(rig)
    second = _row(account.credential_id, 1)
    _owned(service, account, lid='lid1', variant=second.record_id.rsplit(':', 1)[1])
    another = book.with_name('second.epub')
    epub(another, title='Second existing book')
    observed = next(row for row in scan_identity_roots(
        {'default': (book.parents[2],)}).files if row.path == str(another))
    options = dict(observation=observed, **{**scope, 'item_id': second.record_id})
    plan = declare_local_link(service, **options)
    declare_local_link(service, **options, confirmation=plan.plan_token)
    _authorize(broker, broker._source_id(account))
    session.library = lambda: [item, second]
    original = presence.verify_declared_presence
    checked = []
    def verify(*args, **kwargs):
        checked.append(kwargs['item_id'])
        return original(*args, **kwargs)
    monkeypatch.setattr(presence, 'verify_declared_presence', verify)
    ticks = iter([100.0, 109.0])
    monkeypatch.setattr(startup, 'monotonic', lambda: next(ticks))
    result = execute(broker, account.identity_id, 'startup')
    assert not result.ok and result.facts['status'] == 'failed'
    assert result.facts['already_local_declared'] == 1
    assert result.facts['declared_needs_review'] == 1
    assert result.facts['blocked'] == 0
    assert result.facts['attempted'] == result.facts['failed'] == 1
    assert checked == [item.record_id]
    assert book.is_file() and another.is_file()
    assert calls == ['FORBIDDEN DOWNLOAD']
