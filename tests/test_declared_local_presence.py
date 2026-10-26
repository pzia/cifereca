"""#3171: a declared pair can be locally present without an acquisition receipt."""
import json

import pytest

from cifereca.core import library_declared_presence as presence
from tests.test_library_declared_links import rig, apply, rows, records


def check(rig, **overrides):
    service, _book, _root, right, _options = rig
    scope = {key: right[key] for key in (
        'identity_id', 'provider_id', 'credential_id', 'account_id', 'item_id')}
    return presence.verify_declared_presence(
        service, **scope, source_current=lambda: True, **overrides)


def test_fresh_presence_is_read_only_repeatable_and_independent_of_selected_identity(rig, monkeypatch):
    service, book, root, right, _ = rig
    apply(rig)
    monkeypatch.setattr(presence, 'acquisition_lock_file', lambda: root.parent / 'presence.lock')
    before = {name: rows(service, name) for name in (
        'entitlements', 'acquisition_ledger', 'placements', 'artifacts', 'metadata')}
    original = book.read_bytes()
    for owner in ('default', 'alex'):
        service.current_identity = service.identity_by_id(owner)
        def source_current():
            # External source guards may query Storage; never call them while
            # the non-reentrant SQLite mutex is held or hash under that mutex.
            assert not service.storage._lock.locked()
            return service.storage.identity_for_connection(
                right['provider_id'], right['credential_id']) == right['identity_id']
        scope = {key: right[key] for key in presence._SCOPE_FIELDS}
        result = presence.verify_declared_presence(service, **scope, source_current=source_current)
        assert result.status == 'declared_current' and result.reason == 'unchanged'
        assert result.trust == 'user_declaration' and result.read_only
        assert str(book) not in repr(result)
    assert {name: rows(service, name) for name in before} == before
    assert book.read_bytes() == original


@pytest.mark.parametrize('change', ['missing', 'content', 'symlink', 'right', 'connection'])
def test_stale_declarations_are_not_local_presence_or_absence(rig, monkeypatch, change):
    service, book, root, right, _ = rig
    apply(rig)
    monkeypatch.setattr(presence, 'acquisition_lock_file', lambda: root.parent / 'presence.lock')
    before = records(service)
    if change == 'missing':
        book.unlink()
    elif change == 'content':
        book.write_bytes(b'changed')
    elif change == 'symlink':
        target = root / 'another.epub'
        book.rename(target)
        book.symlink_to(target)
    elif change == 'right':
        service.storage.entitlement_put({**right, 'state': 'inactive'})
    else:
        service.storage.provider_connection_put({
            'provider_id': right['provider_id'], 'credential_id': right['credential_id'],
            'identity_id': 'alex',
        })
    result = check(rig)
    assert result.status == 'needs_review'
    assert records(service) == before
    assert rows(service, 'acquisition_ledger') == rows(service, 'placements') == []


def test_a_pair_over_the_byte_budget_remains_for_review(rig, monkeypatch):
    service, _, root, _, _ = rig
    apply(rig)
    monkeypatch.setattr(presence, 'acquisition_lock_file', lambda: root.parent / 'presence.lock')
    result = check(rig, max_bytes=1)
    assert result.status == 'needs_review' and result.reason == 'byte_budget'


@pytest.mark.parametrize('change', ['right', 'declaration', 'root', 'cancel'])
def test_mutation_during_hash_is_revalidated_before_reporting_presence(rig, monkeypatch, change):
    service, book, root, right, _ = rig
    result = apply(rig)
    monkeypatch.setattr(presence, 'acquisition_lock_file', lambda: root.parent / 'presence.lock')
    original = presence.revalidate_observed_file
    cancelled = [False]
    def hashing(*args, **kwargs):
        assert not service.storage._lock.locked()
        checked = original(*args, **kwargs)
        assert checked.verified
        if change == 'right':
            service.storage.entitlement_put({**right, 'state': 'inactive'})
        elif change == 'declaration':
            record, = records(service)
            record.update(state='revoked', revoked_at=123)
            service.storage.metadata_set(
                'core.library.user-declaration.v1.' + result.relation_id, json.dumps(record))
        elif change == 'root':
            service.config.set('library.directory', str(root.parent / 'new-root'))
        else:
            cancelled[0] = True
        return checked
    monkeypatch.setattr(presence, 'revalidate_observed_file', hashing)
    checked = check(rig, cancelled=lambda: cancelled[0])
    assert checked.status == 'needs_review'
    assert rows(service, 'acquisition_ledger') == rows(service, 'placements') == []
    assert book.is_file()
