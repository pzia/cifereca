"""#2797: persisted declarations are visible after sync, without attestation."""
from __future__ import annotations

import pytest

from cifereca.core.library_declared_links import (
    DeclarationRejected, list_declared_links,
)
from tests.test_library_declared_links import apply, inspect, rig, records, rows


def test_active_identity_sees_stable_nonattested_link_after_two_syncs(rig):
    service, book, _, right, options = rig
    before = {name: rows(service, name) for name in (
        'artifacts', 'placements', 'backup_items', 'acquisition_ledger',
    )}
    result = apply(rig)
    original = records(service)

    for _ in range(2):
        service.storage.entitlement_put(right)
        page = list_declared_links(service, identity_id='default')
        assert page.total == 1 and not page.limited
        item, = page.rows
        assert item.relation_id == result.relation_id
        assert item.provider_id == options['provider_id']
        assert item.file_path == str(book)
        assert item.state == 'active'
        assert item.review == 'recorded_unverified_file'
        assert 'PRIVATE' not in repr(page) and 'PRIVATE' not in repr(item)
        assert records(service) == original

    service.storage.entitlement_put({**right, 'state': 'expired'})
    after = list_declared_links(service, identity_id='default')
    assert after.total == 1
    assert after.rows[0].review == 'source_or_right_changed'
    assert {name: rows(service, name) for name in before} == before
    assert records(service) == original


def test_read_only_listing_ignores_other_profile_even_with_known_identity_id(rig):
    service, _, _, _, _ = rig
    apply(rig)
    assert list_declared_links(service, identity_id='default').total == 1
    with pytest.raises(DeclarationRejected, match='identity_not_active'):
        list_declared_links(service, identity_id='alex')
    service.current_identity = service.identity_by_id('alex')
    assert list_declared_links(service, identity_id='alex').total == 0
    with pytest.raises(DeclarationRejected, match='identity_not_active'):
        list_declared_links(service, identity_id='default')


def test_revoked_declaration_is_optional_history_not_an_active_link(rig):
    service, _, _, _, _ = rig
    from tests.test_library_declared_links import call
    result = apply(rig)
    plan = call(rig, revoke=result.relation_id)
    assert call(rig, revoke=result.relation_id,
                confirmation=plan.plan_token).status == 'revoked'
    assert list_declared_links(service, identity_id='default').total == 0
    archived = list_declared_links(service, identity_id='default',
                                  include_revoked=True)
    assert archived.total == 1
    assert archived.rows[0].state == archived.rows[0].review == 'revoked'


def test_closed_pagination_and_malformed_metadata_fail_closed(rig):
    service, _, _, _, _ = rig
    apply(rig)
    with pytest.raises(DeclarationRejected, match='invalid_page'):
        list_declared_links(service, identity_id='default', offset=True)
    with pytest.raises(DeclarationRejected, match='invalid_page'):
        list_declared_links(service, identity_id='default', limit=0)
    with pytest.raises(DeclarationRejected, match='invalid_page'):
        list_declared_links(service, identity_id='default', include_revoked=1)
    page = list_declared_links(service, identity_id='default', offset=1)
    assert page.total == 1 and page.rows == () and not page.limited

    # A damaged namespace must not be silently ignored and yield a falsely
    # reassuring "no declarations" result.
    with service.storage._lock, service.storage._connect() as db:
        db.execute('INSERT INTO metadata(key,value) VALUES(?,?)',
                   ('core.library.user-declaration.v1.bad', '{BROKEN'))
    with pytest.raises(DeclarationRejected, match='invalid_local_context'):
        list_declared_links(service, identity_id='default')


@pytest.mark.parametrize('account_drift', (
    'removed', 'replaced_account', 'reassigned_identity', 'duplicated'
))
def test_current_provider_account_drift_is_visible_without_erasing_history(
        rig, account_drift):
    """SQLite rights can remain after the actual provider login is removed."""
    from dataclasses import replace

    service, book, _, _, _ = rig
    created = apply(rig)
    baseline = records(service)
    original = service.accounts
    previous = list_declared_links(service, identity_id='default')
    assert previous.rows[0].review == 'recorded_unverified_file'
    if account_drift == 'removed':
        service.accounts = []
    elif account_drift == 'replaced_account':
        service.accounts = [replace(original[0], unique_id='DIFFERENT-account')]
    elif account_drift == 'reassigned_identity':
        service.accounts = [replace(original[0], identity_id='alex')]
    else:
        service.accounts = [original[0], original[0]]

    stale = list_declared_links(service, identity_id='default')
    assert stale.total == 1 and len(stale.rows) == 1
    assert stale.rows[0].relation_id == created.relation_id
    assert stale.rows[0].file_path == str(book)
    assert stale.rows[0].review == 'source_or_right_changed'
    assert records(service) == baseline
    assert not rows(service, 'placements')
    assert not rows(service, 'acquisition_ledger')

    # A later account sync can repair the context, not the declaration.
    service.accounts = original
    restored = list_declared_links(service, identity_id='default')
    assert restored.rows[0].review == 'recorded_unverified_file'
    assert records(service) == baseline


def test_disabled_provider_connection_is_reviewed_not_silently_attested(rig):
    service, _, _, _, options = rig
    result = apply(rig)
    baseline = records(service)
    assert list_declared_links(service, identity_id='default').rows[0].review == (
        'recorded_unverified_file'
    )
    # A service can retain an account object after a sync disables the local
    # provider connection; the persisted right is not current provenance.
    connection = {
        'identity_id': 'default',
        'provider_id': options['provider_id'],
        'credential_id': options['credential_id'],
        'label': 'Fixture disabled connection',
        'enabled': False,
    }
    service.storage.provider_connection_put(connection)
    page = list_declared_links(service, identity_id='default')
    assert page.total == 1
    assert page.rows[0].relation_id == result.relation_id
    assert page.rows[0].review == 'source_or_right_changed'
    # The detailed inspector must agree: unchanged EPUB/SHA cannot make a
    # disabled source look like a current supplier declaration.
    assert inspect(rig, result.relation_id).status == 'needs_review'
    assert records(service) == baseline
    assert not rows(service, 'placements')
    assert not rows(service, 'acquisition_ledger')
    service.storage.provider_connection_put({**connection, 'enabled': True})
    page = list_declared_links(service, identity_id='default')
    assert page.rows[0].review == 'recorded_unverified_file'
    assert inspect(rig, result.relation_id).status == 'declared_current'
