"""Opaque provider coordinates retain declaration and file isolation (#3171)."""
import pytest

from cifereca.core import library_declared_links as engine
from cifereca.core import library_declared_presence as presence
from cifereca.core.library_inventory import scan_identity_roots
from cifereca.models import Account
from tests.test_library_declared_links import rig, apply, call, records, rows


@pytest.mark.parametrize('rig', ['test.vendor-a', 'test.vendor-b'], indirect=True)
def test_distinct_provider_coordinates_never_turn_equal_bytes_into_authority(rig, monkeypatch):
    service, book, root, right, options = rig
    monkeypatch.setattr(presence, 'acquisition_lock_file', lambda: root.parent / 'presence.lock')
    first = apply(rig)
    other_provider = 'test.vendor-b' if right['provider_id'] == 'test.vendor-a' else 'test.vendor-a'
    other_scope = {key: right[key] for key in engine._SCOPE_FIELDS}
    other_scope['provider_id'] = other_provider

    def check(scope, source_current=lambda: True):
        return presence.verify_declared_presence(service, **scope, source_current=source_current)

    # Matching account/item/credential strings and bytes do not supply the
    # other provider's account, connection or entitlement.
    with pytest.raises(engine.DeclarationRejected, match='account_scope_changed'):
        call(rig, provider_id=other_provider)
    service.accounts.append(Account(
        display_name='Second source', unique_id=right['account_id'],
        provider_id=other_provider, credential_id=right['credential_id'],
        provider_label=other_provider, identity_id=right['identity_id']))
    with pytest.raises(engine.DeclarationRejected, match='source_scope_changed'):
        call(rig, provider_id=other_provider)
    service.storage.provider_connection_put({
        **{key: other_scope[key] for key in ('identity_id', 'provider_id', 'credential_id')},
        'label': 'Second source',
    })
    with pytest.raises(engine.DeclarationRejected, match='right_scope_changed'):
        call(rig, provider_id=other_provider)
    assert check(other_scope).status == 'needs_review'
    assert not engine.has_active_declared_link(service.storage, **other_scope)

    service.storage.entitlement_put({
        **right, 'entitlement_id': 'second-right', 'provider_id': other_provider})
    with pytest.raises(engine.DeclarationRejected, match='declaration_conflict'):
        call(rig, provider_id=other_provider)
    alias = root / 'alias.epub'
    alias.hardlink_to(book)
    alias_observation = next(o for o in scan_identity_roots({'default': (root,)}).files
                             if o.path == str(alias))
    with pytest.raises(engine.DeclarationRejected, match='declaration_conflict'):
        call(rig, provider_id=other_provider, observation=alias_observation)
    alias.unlink()

    copy = root / 'independent-copy.epub'
    copy.write_bytes(book.read_bytes())
    observations = scan_identity_roots({'default': (root,)}).files
    original = next(o for o in observations if o.path == str(book))
    independent = next(o for o in observations if o.path == str(copy))
    assert original.sha256 == independent.sha256
    assert original.fingerprint[:2] != independent.fingerprint[:2]
    # Creating/removing a hardlink changed the original fingerprint. Obtain
    # explicit confirmation of the now-current original file before reuse.
    assert check({key: options[key] for key in engine._SCOPE_FIELDS}).status == 'needs_review'
    revocation = call(rig, revoke=first.relation_id)
    call(rig, revoke=first.relation_id, confirmation=revocation.plan_token)
    first = apply((service, book, root, right, {**options, 'observation': original}))
    preview = call(rig, provider_id=other_provider, observation=independent)
    second = call(rig, provider_id=other_provider, observation=independent,
                  confirmation=preview.plan_token)
    assert second.status == 'declared'
    names = ('entitlements', 'acquisition_ledger', 'backup_items', 'artifacts',
             'placements', 'catalog_entries', 'provider_connections', 'metadata')
    before = {name: rows(service, name) for name in names}
    for selected_identity in ('default', 'alex'):
        service.current_identity = service.identity_by_id(selected_identity)
        for scope in (options, other_scope):
            exact = {key: scope[key] for key in engine._SCOPE_FIELDS}
            assert check(exact).status == 'declared_current'
            assert engine.has_active_declared_link(service.storage, **exact)
            assert check(exact, source_current=lambda: False).status == 'needs_review'
            assert check({**exact, 'item_id': 'another-edition'}).status == 'needs_review'
            assert check({**exact, 'identity_id': 'alex'}).status == 'needs_review'
    assert {name: rows(service, name) for name in names} == before
    assert book.read_bytes() == copy.read_bytes()
    service.current_identity = service.identity_by_id('default')
    revocation = call(rig, provider_id=other_provider, observation=independent,
                      revoke=second.relation_id)
    call(rig, provider_id=other_provider, observation=independent,
         revoke=second.relation_id, confirmation=revocation.plan_token)
    exact = {key: options[key] for key in engine._SCOPE_FIELDS}
    assert check(exact).status == 'declared_current'
    assert check(other_scope).status == 'needs_review'
    assert not engine.has_active_declared_link(service.storage, **other_scope)
    assert next(r for r in records(service) if r['relation_id'] == first.relation_id)['state'] == 'active'
