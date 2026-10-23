"""#2797: stale declaration is withdrawable after the book/account disappears."""
from __future__ import annotations

import pytest

from cifereca.core.library_declared_links import (
    DeclarationRejected, list_declared_links, withdraw_declared_link,
)
from tests.test_library_declared_links import apply, records, rig, rows


def test_withdraw_missing_book_removed_right_and_unavailable_account(rig):
    service, book, _, _, _ = rig
    result = apply(rig)
    book.unlink()
    with service.storage._lock, service.storage._connect() as db:
        db.execute('DELETE FROM entitlements')
    service.accounts = []
    untouched = {name: rows(service, name) for name in (
        'entitlements', 'provider_connections', 'acquisition_ledger',
        'artifacts', 'placements', 'backup_items',
    )}
    page = list_declared_links(service, identity_id='default')
    assert page.total == 1 and page.rows[0].review == 'source_or_right_changed'
    plan = withdraw_declared_link(service, identity_id='default',
                                  relation_id=result.relation_id)
    assert plan.status == 'revoke_preview'
    assert plan.before['origin'] == 'user_declaration'
    assert plan.changed == 0
    assert records(service)[0]['state'] == 'active'
    calls = []
    answer = withdraw_declared_link(
        service, identity_id='default', relation_id=result.relation_id,
        confirmation=plan.plan_token, on_committed=lambda: calls.append(True),
    )
    assert answer.status == 'revoked' and answer.changed == 1
    assert calls == [True]
    assert records(service)[0]['state'] == 'revoked'
    assert records(service)[0]['revoked_at'] is not None
    assert list_declared_links(service, identity_id='default').total == 0
    history = list_declared_links(service, identity_id='default',
                                  include_revoked=True)
    assert history.total == 1 and history.rows[0].review == 'revoked'
    assert withdraw_declared_link(
        service, identity_id='default', relation_id=result.relation_id,
    ).status == 'already_revoked'
    assert {name: rows(service, name) for name in untouched} == untouched
    assert not book.exists()


def test_preview_revocation_is_read_only_and_stale_token_rejected(rig):
    service, _, _, _, _ = rig
    result = apply(rig)
    plan = withdraw_declared_link(service, identity_id='default',
                                  relation_id=result.relation_id)
    before = records(service)
    assert plan.status == 'revoke_preview'
    assert before[0]['state'] == 'active'
    with pytest.raises(DeclarationRejected, match='^preview_changed$'):
        withdraw_declared_link(service, identity_id='default',
                               relation_id=result.relation_id,
                               confirmation='0' * 64)
    assert records(service) == before
    with pytest.raises(DeclarationRejected, match='^source_scope_changed$'):
        withdraw_declared_link(service, identity_id='default',
                               relation_id=result.relation_id,
                               confirmation=plan.plan_token, cancelled=lambda: True)
    assert records(service) == before
    # A legitimate other write invalidates a preview, rather than rewriting
    # it blindly at commit. Do not discard the source lineage.
    with service.storage._lock, service.storage._connect() as db:
        db.execute('UPDATE metadata SET value=? WHERE key=?', (
            __import__('json').dumps({**before[0], 'created_at': before[0]['created_at'] + 1}),
            'core.library.user-declaration.v1.' + result.relation_id))
    with pytest.raises(DeclarationRejected, match='^preview_changed$'):
        withdraw_declared_link(service, identity_id='default',
                               relation_id=result.relation_id,
                               confirmation=plan.plan_token)
    assert records(service)[0]['state'] == 'active'


def test_cross_profile_and_bad_relation_never_revoke(rig):
    service, _, _, _, _ = rig
    result = apply(rig)
    previous = records(service)
    with pytest.raises(DeclarationRejected, match='^identity_not_active$'):
        withdraw_declared_link(service, identity_id='alex',
                               relation_id=result.relation_id)
    with pytest.raises(DeclarationRejected, match='^invalid_relation$'):
        withdraw_declared_link(service, identity_id='default',
                               relation_id='not-an-id')
    with pytest.raises(DeclarationRejected, match='^relation_scope_changed$'):
        withdraw_declared_link(service, identity_id='default',
                               relation_id='0' * 32)
    service.current_identity = service.identity_by_id('alex')
    with pytest.raises(DeclarationRejected, match='^relation_scope_changed$'):
        withdraw_declared_link(service, identity_id='alex',
                               relation_id=result.relation_id)
    assert records(service) == previous


def test_acquisition_lock_or_store_failure_cannot_partially_withdraw(rig):
    from cifereca.locking import InterProcessLock
    from cifereca.core import library_declared_links as engine
    service, _, _, _, _ = rig
    result = apply(rig)
    plan = withdraw_declared_link(service, identity_id='default',
                                  relation_id=result.relation_id)
    with InterProcessLock(engine.acquisition_lock_file()):
        with pytest.raises(DeclarationRejected, match='^acquisition_busy$'):
            withdraw_declared_link(service, identity_id='default',
                                   relation_id=result.relation_id,
                                   confirmation=plan.plan_token)
    with service.storage._lock, service.storage._connect() as db:
        db.execute("CREATE TRIGGER deny_withdraw BEFORE UPDATE ON metadata "
                   "WHEN NEW.key GLOB 'core.library.user-declaration.v1.*' "
                   "BEGIN SELECT RAISE(ABORT,'PRIVATE content'); END")
    with pytest.raises(DeclarationRejected, match='^local_storage_unavailable$'):
        withdraw_declared_link(service, identity_id='default',
                               relation_id=result.relation_id,
                               confirmation=plan.plan_token)
    assert records(service)[0]['state'] == 'active'
    assert not rows(service, 'placements')
