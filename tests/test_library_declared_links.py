"""#2797: user statements survive sync without becoming supplier evidence."""
from dataclasses import replace
import json
import sqlite3

import pytest

from cifereca.core import library_declared_links as engine
from cifereca.core.library_audit import audit_library
from cifereca.core.library_declared_links import (
    DeclarationRejected, declare_local_link, inspect_declared_link,
)
from cifereca.core.library_inventory import scan_identity_roots
from cifereca.models import Account
from tests.test_library_reconciliation import epub
from tests.test_sync_state_contract import _service


@pytest.fixture(params=['numilog', 'kobo', 'paris', 'test.vendor-a', 'test.vendor-b'])
def rig(request, tmp_path, monkeypatch):
    service = _service(tmp_path)
    root = tmp_path / 'EBooks' / 'hugues'
    root.mkdir(parents=True, exist_ok=True)
    book = root / 'PRIVATE-book.epub'
    epub(book, title='PRIVATE title')
    observation, = scan_identity_roots({'default': (root,)}).files
    provider = request.param
    account = Account(display_name='PRIVATE account', unique_id='PRIVATE-account',
                      provider_id=provider, credential_id='PRIVATE-credential',
                      provider_label=provider, identity_id='default')
    service.accounts = [account]
    service.storage.provider_connection_put({
        'identity_id': 'default', 'provider_id': provider,
        'credential_id': account.credential_id, 'label': provider,
    })
    right = {
        'entitlement_id': 'PRIVATE-right', 'provider_id': provider,
        'credential_id': account.credential_id, 'account_id': account.unique_id,
        'identity_id': 'default', 'item_id': 'PRIVATE-item',
        'entitlement_type': 'loan' if provider == 'paris' else 'owned',
        'state': 'active', 'metadata': {'title': 'PRIVATE title'},
    }
    service.storage.entitlement_put(right)
    monkeypatch.setattr(engine, 'acquisition_lock_file', lambda: tmp_path / 'acquire.lock')
    options = {'observation': observation, **{k: right[k] for k in engine._SCOPE_FIELDS}}
    try:
        yield service, book, root, right, options
    finally:
        service.close()


def rows(service, name):
    with service.storage._lock, service.storage._connect() as db:
        return db.execute('SELECT * FROM ' + name + ' ORDER BY 1').fetchall()


def records(service):
    return [json.loads(value) for key, value in rows(service, 'metadata')
            if key.startswith(engine._PREFIX)]


def call(rig, **overrides):
    service, _, _, _, options = rig
    return declare_local_link(service, **{**options, **overrides})


def inspect(rig, relation_id, **overrides):
    service, _, _, _, options = rig
    return inspect_declared_link(service, relation_id=relation_id, **{**options, **overrides})


def apply(rig):
    plan = call(rig)
    assert plan.status == 'preview' and plan.changed == 0
    assert plan.before is None and plan.after['origin'] == 'user_declaration'
    assert plan.after['scope']['provider_id'] == rig[4]['provider_id']
    return call(rig, confirmation=plan.plan_token)


def test_confirmed_declaration_is_separate_durable_reversible_and_idempotent(rig):
    service, book, root, _, options = rig
    original = book.read_bytes()
    names = ('entitlements', 'acquisition_ledger', 'backup_items', 'artifacts',
             'placements', 'catalog_entries', 'provider_connections')
    before = {name: rows(service, name) for name in names}
    metadata_before = rows(service, 'metadata')
    preview = call(rig)
    assert rows(service, 'metadata') == metadata_before
    assert {name: rows(service, name) for name in names} == before
    assert 'PRIVATE' not in repr(preview) and options['observation'].sha256 not in repr(preview)
    result = call(rig, confirmation=preview.plan_token)
    assert result.status == 'declared' and result.changed == 1
    assert result.rights_mutations == result.placement_mutations == result.file_mutations == 0
    assert call(rig, confirmation=preview.plan_token).status == 'already_declared'
    assert len(records(service)) == 1
    assert inspect(rig, result.relation_id).status == 'declared_current'
    audit = audit_library(service.storage.path, {'default': (root,)})
    assert not audit.reconciliation.edges  # metadata is never a confirmed edge
    assert 'PRIVATE' not in json.dumps(audit.redacted())
    revocation = call(rig, revoke=result.relation_id)
    assert revocation.status == 'revoke_preview'
    assert records(service)[0]['state'] == 'active'
    revoked = call(rig, revoke=result.relation_id, confirmation=revocation.plan_token)
    assert revoked.status == 'revoked' and revoked.changed == 1
    assert inspect(rig, result.relation_id).status == 'revoked'
    assert call(rig, revoke=result.relation_id).status == 'already_revoked'
    assert records(service)[0]['origin'] == 'user_declaration'
    assert records(service)[0]['revoked_at'] is not None
    # A token from BEFORE the declaration/revocation cannot reapply it.
    with pytest.raises(DeclarationRejected, match='preview_changed'):
        call(rig, confirmation=preview.plan_token)
    assert {name: rows(service, name) for name in names} == before
    assert book.read_bytes() == original


def test_sync_twice_preserves_declaration_and_changed_loan_needs_review(rig):
    service, _, _, right, _ = rig
    result = apply(rig)
    stored = records(service)
    for _ in range(2):
        service.storage.entitlement_put(right)
        assert inspect(rig, result.relation_id).status == 'declared_current'
        assert records(service) == stored
    service.storage.entitlement_put({**right, 'state': 'expired'})
    assert inspect(rig, result.relation_id).status == 'needs_review'
    assert records(service) == stored
    assert not rows(service, 'acquisition_ledger')
    assert not rows(service, 'placements')


@pytest.mark.parametrize('change,code', [
    ('identity', 'identity_not_active'), ('account', 'account_scope_changed'),
    ('right', 'right_scope_changed'), ('file', 'file_revalidation_failed'),
    ('symlink', 'file_revalidation_failed'), ('scope_epoch', 'preview_changed'),
    ('sql', 'preview_changed'), ('cancel', 'source_scope_changed'),
    ('observation_identity', 'file_scope_changed'),
])
def test_stale_preview_cross_account_and_changed_file_cannot_write(rig, change, code):
    service, book, _, _, options = rig
    plan = call(rig)
    overrides = {}
    if change == 'identity':
        overrides['identity_id'] = 'other'
    elif change == 'account':
        service.accounts[0].unique_id = 'other'
    elif change == 'right':
        with service.storage._lock, service.storage._connect() as db:
            db.execute("UPDATE entitlements SET account_id='other'")
    elif change == 'file':
        epub(book, title='changed')
    elif change == 'symlink':
        target = book.with_name('target.epub')
        book.rename(target)
        book.symlink_to(target)
    elif change == 'scope_epoch':
        service._account_context_epoch += 1
    elif change == 'sql':
        with service.storage._lock, service.storage._connect() as db:
            db.execute("UPDATE entitlements SET state='expired'")
    elif change == 'cancel':
        overrides['cancelled'] = lambda: True
    elif change == 'observation_identity':
        overrides['observation'] = replace(options['observation'], identity_id='other')
    with pytest.raises(DeclarationRejected, match=code):
        call(rig, confirmation=plan.plan_token, **overrides)
    assert records(service) == []
    assert not rows(service, 'placements')


def test_second_file_same_right_conflicts_and_external_edit_is_visible(rig):
    _, book, root, _, _ = rig
    result = apply(rig)
    second = book.with_name('second.epub')
    second.write_bytes(book.read_bytes())
    observations = scan_identity_roots({'default': (root,)}).files
    other = next(o for o in observations if o.path == str(second))
    with pytest.raises(DeclarationRejected, match='declaration_conflict'):
        call(rig, observation=other)
    assert inspect(rig, result.relation_id, observation=other).status == 'needs_review'
    epub(book, title='external edit')
    assert inspect(rig, result.relation_id).status == 'needs_review'


def test_final_revalidation_failure_rolls_back_exactly(rig, monkeypatch):
    from cifereca.core.library_fresh_file_check import FreshFileCheck
    service = rig[0]
    plan = call(rig)
    before = rows(service, 'metadata')
    validate = engine.revalidate_observed_file
    calls = 0

    def race(*args, **kwargs):
        nonlocal calls
        calls += 1
        return validate(*args, **kwargs) if calls == 1 else FreshFileCheck(False, 'file_changed')

    monkeypatch.setattr(engine, 'revalidate_observed_file', race)
    with pytest.raises(DeclarationRejected, match='file_changed_before_commit'):
        call(rig, confirmation=plan.plan_token)
    assert rows(service, 'metadata') == before


def test_corrupt_namespace_never_hides_a_conflict_or_leaks_error(rig):
    service = rig[0]
    with service.storage._lock, service.storage._connect() as db:
        db.execute('INSERT INTO metadata(key,value) VALUES(?,?)',
                   (engine._PREFIX + 'a' * 32, '{PRIVATE broken'))
    with pytest.raises(DeclarationRejected) as error:
        call(rig)
    assert str(error.value) == 'invalid_local_context'
    assert 'PRIVATE' not in repr(error.value)


def test_same_file_second_right_and_foreign_placement_are_refused(rig):
    service, book, _, right, options = rig
    apply(rig)
    service.storage.entitlement_put({**right, 'entitlement_id': 'second', 'item_id': 'second'})
    with pytest.raises(DeclarationRejected, match='declaration_conflict'):
        call(rig, item_id='second')
    with service.storage._lock, service.storage._connect() as db:
        db.execute('INSERT INTO placements(placement_id,artifact_id,path,identity_id,'
                   'provider_id,source_item_id,state,created_at,updated_at) '
                   "VALUES('foreign',?,?, 'other','kobo','foreign','present',1,1)",
                   ('sha256:' + options['observation'].sha256, str(book)))
    with pytest.raises(DeclarationRejected, match='placement_conflict'):
        call(rig)


def test_revocation_is_not_general_metadata_delete_and_requires_fresh_consent(rig):
    service = rig[0]
    result = apply(rig)
    before = records(service)
    with pytest.raises(DeclarationRejected, match='relation_scope_changed'):
        call(rig, revoke='0' * 32)
    with pytest.raises(DeclarationRejected, match='preview_changed'):
        call(rig, revoke=result.relation_id, confirmation='0' * 64)
    assert records(service) == before


def test_foreign_catalogue_reference_on_local_placement_is_not_hijacked(rig):
    service, book, _, _, options = rig
    with service.storage._lock, service.storage._connect() as db:
        db.execute('INSERT INTO placements(placement_id,artifact_id,path,identity_id,'
                   'provider_id,source_item_id,state,created_at,updated_at) '
                   "VALUES('local',?,?,'default','local',NULL,'present',1,1)",
                   ('sha256:' + options['observation'].sha256, str(book)))
        db.execute('INSERT INTO catalog_entries(catalog_entry_id,identity_id,provider_id,'
                   'item_id,placement_id,title,local_state,metadata_json,created_at,updated_at) '
                   "VALUES('foreign','alex','kobo','other','local','PRIVATE','local','{}',1,1)")
    with pytest.raises(DeclarationRejected, match='catalogue_conflict'):
        call(rig)
    assert records(service) == []


def test_sql_failure_rolls_back_without_changing_rights_or_file(rig):
    service, book, _, _, _ = rig
    plan = call(rig)
    original = book.read_bytes()
    before = rows(service, 'metadata')
    with service.storage._lock, service.storage._connect() as db:
        db.execute("CREATE TRIGGER declaration_failure BEFORE INSERT ON metadata "
                   "WHEN NEW.key GLOB 'core.library.user-declaration.v1.*' "
                   "BEGIN SELECT RAISE(ABORT,'PRIVATE sql detail'); END")
    with pytest.raises(DeclarationRejected, match='^local_storage_unavailable$'):
        call(rig, confirmation=plan.plan_token)
    assert rows(service, 'metadata') == before
    assert book.read_bytes() == original
    assert not rows(service, 'placements')


def test_acquisition_lock_conflict_keeps_preview_read_only(rig):
    from cifereca.locking import InterProcessLock
    service = rig[0]
    plan = call(rig)
    with InterProcessLock(engine.acquisition_lock_file()):
        assert call(rig).status == 'preview'
        with pytest.raises(DeclarationRejected, match='^acquisition_busy$'):
            call(rig, confirmation=plan.plan_token)
    assert records(service) == []


def test_scan_local_placement_keeps_user_origin_and_never_attests(rig):
    service, book, root, _, options = rig
    result = apply(rig)
    stored = records(service)
    # Same effect as a subsequent local index: a neutral local placement.
    service.catalog_index_path(book, identity_id='default', provider_id='local')
    assert records(service) == stored
    assert inspect(rig, result.relation_id).status == 'declared_current'
    audit = audit_library(service.storage.path, {'default': (root,)})
    assert all(edge.certainty != 'attested' for edge in audit.reconciliation.edges)
    assert options['observation'].sha256 not in json.dumps(audit.redacted())


def test_declared_loan_survives_realized_sync_without_becoming_permanent(rig):
    service, _, _, _, options = rig
    if options['provider_id'] != 'paris':
        return
    result = apply(rig)
    stored = records(service)
    for _ in range(2):
        # An explicitly attested complete source may close this one loan;
        # an unverified empty opening listing must never do so.
        right_row = service.storage.entitlements()[0]
        service.storage.entitlement_realize_missing_loans(
            set(), identity_id='default',
            complete_scopes={(
                right_row['provider_id'], right_row['credential_id'],
                right_row['account_id'],
            )},
        )
        assert inspect(rig, result.relation_id).status == 'needs_review'
        assert records(service) == stored
    assert service.storage.entitlements()[0]['entitlement_type'] == 'loan'
    assert not rows(service, 'placements')


def test_missing_file_and_deleted_right_do_not_prevent_withdrawing_statement(rig):
    service, book, _, _, _ = rig
    result = apply(rig)
    book.unlink()
    with service.storage._lock, service.storage._connect() as db:
        db.execute('DELETE FROM entitlements')
    plan = call(rig, revoke=result.relation_id)
    assert plan.status == 'revoke_preview'
    assert call(rig, revoke=result.relation_id, confirmation=plan.plan_token).status == 'revoked'
    assert records(service)[0]['state'] == 'revoked'


def test_disabled_connection_cannot_create_new_declaration_but_history_can_be_withdrawn(rig):
    service, book, _, _, options = rig
    original = book.read_bytes()
    # First create an explicit, lower-trust statement while the source is live.
    created = apply(rig)
    assert inspect(rig, created.relation_id).status == 'declared_current'
    before = records(service)
    with service.storage._lock, service.storage._connect() as db:
        db.execute("UPDATE provider_connections SET enabled=0 WHERE provider_id=? AND credential_id=?",
                   (options['provider_id'], options['credential_id']))
    assert inspect(rig, created.relation_id).status == 'needs_review'
    assert records(service) == before
    # A second file/right selection cannot create new evidence from a
    # disabled connection, even though its stale SQLite entitlement remains.
    with pytest.raises(DeclarationRejected, match='^source_connection_disabled$'):
        call(rig)
    assert records(service) == before
    assert not rows(service, 'acquisition_ledger')
    assert not rows(service, 'placements')
    # Undo is always possible: disabling the supplier must not trap history.
    undo = engine.withdraw_declared_link(
        service, identity_id='default', relation_id=created.relation_id,
    )
    assert undo.status == 'revoke_preview'
    result = engine.withdraw_declared_link(
        service, identity_id='default', relation_id=created.relation_id,
        confirmation=undo.plan_token,
    )
    assert result.status == 'revoked'
    assert records(service)[0]['state'] == 'revoked'
    assert book.read_bytes() == original


def test_disabling_connection_between_preview_and_confirmation_fails_closed(rig):
    service, book, _, _, options = rig
    preview = call(rig)
    assert preview.status == 'preview'
    with service.storage._lock, service.storage._connect() as db:
        db.execute("UPDATE provider_connections SET enabled=0 WHERE provider_id=? AND credential_id=?",
                   (options['provider_id'], options['credential_id']))
    with pytest.raises(DeclarationRejected, match='^source_connection_disabled$'):
        call(rig, confirmation=preview.plan_token)
    assert not records(service)
    assert not rows(service, 'acquisition_ledger')
    assert not rows(service, 'placements')
