"""#2797: persisted declarations are visible after sync, without attestation."""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.library_declared_links import (
    DeclarationRejected, list_declared_links,
)
from tests.test_library_declared_links import apply, rig, records, rows


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


def test_removed_changed_or_ambiguous_live_account_requires_review(rig):
    """A stale SQLite right is not proof that the provider account is active."""
    service, book, _, right, _ = rig
    original_accounts = service.accounts
    result = apply(rig)
    baseline = {table: rows(service, table) for table in (
        'entitlements', 'acquisition_ledger', 'artifacts', 'placements',
        'backup_items', 'metadata',
    )}
    original_bytes = book.read_bytes()

    def view():
        page = list_declared_links(service, identity_id='default')
        assert page.total == 1
        item, = page.rows
        assert item.relation_id == result.relation_id
        return item

    assert view().review == 'recorded_unverified_file'
    service.accounts = []
    assert view().review == 'source_or_right_changed'
    service.accounts = [replace(original_accounts[0], unique_id='other-account')]
    assert view().review == 'source_or_right_changed'
    service.accounts = original_accounts + original_accounts
    assert view().review == 'source_or_right_changed'

    # A genuine reappearance after a provider sync can restore the
    # conservative recorded-only state, never supplier attestation.
    service.accounts = original_accounts
    service.storage.entitlement_put(right)
    assert view().review == 'recorded_unverified_file'
    assert {table: rows(service, table) for table in baseline} == baseline
    assert book.read_bytes() == original_bytes


def test_disabled_connection_never_looks_like_current_provider_provenance(rig):
    service, book, _, right, options = rig
    result = apply(rig)
    baseline = {table: rows(service, table) for table in (
        'entitlements', 'acquisition_ledger', 'artifacts', 'placements',
        'backup_items', 'metadata',
    )}

    def view():
        item, = list_declared_links(service, identity_id='default').rows
        assert item.relation_id == result.relation_id
        return item

    assert view().review == 'recorded_unverified_file'
    service.storage.provider_connection_put({
        'identity_id': 'default',
        'provider_id': options['provider_id'],
        'credential_id': options['credential_id'],
        'label': 'Synthetic connection',
        'enabled': False,
    })
    assert view().review == 'source_or_right_changed'
    assert list_declared_links(service, identity_id='default',
                               include_revoked=True).total == 1
    service.storage.provider_connection_put({
        'identity_id': 'default',
        'provider_id': options['provider_id'],
        'credential_id': options['credential_id'],
        'label': 'Synthetic connection',
        'enabled': True,
    })
    assert view().review == 'recorded_unverified_file'
    assert {table: rows(service, table) for table in baseline} == baseline
    assert book.is_file()
