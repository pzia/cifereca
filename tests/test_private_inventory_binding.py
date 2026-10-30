"""One pinned private-library binding for every provider, including v1 hosts."""
from copy import copy
from types import SimpleNamespace as NS
import unittest

from cifereca.core.private_inventory_binding import private_inventory_binding_current
from cifereca.core.sync_coordination import _synchronize_account_source_impl
from cifereca.providers import ProviderConfigurationError


def rig(provider='fixture-unknown', account_id='buyer'):
    account = NS(identity_id='alice', provider_id=provider,
                 credential_id='connection-a', unique_id=account_id)
    scope = ('alice', provider, 'connection-a', account_id)
    state = NS(owner='alice', enabled=True, profile=True,
               connections=[dict(provider_id=provider, credential_id='connection-a', enabled=True)])
    session = NS(provider_id=provider, credential_id='connection-a', connected=True,
                 capabilities=NS(loans=False, personal_library=True))
    service = NS(accounts=[account], _sessions={(provider, 'connection-a'): session},
                 _source_context_epoch=3, current_identity=NS(identity_id='bob'),
                 _session_key=lambda p,c:(p,c),
                 sync_source_enabled=lambda a: state.enabled,
                 identity_by_id=lambda identity: NS(identity_id=identity) if state.profile else None,
                 storage=NS(identity_for_connection=lambda p,c:state.owner,
                            provider_connections=lambda **kw:state.connections))
    service._session_for_account = lambda a: session
    service.reads, service.projections = [], []
    def library():
        service.reads.append(1)
        return [object()]
    def project(rows, *, identity_id):
        service.projections.append((len(rows), identity_id))
        return len(rows)
    session.library = library
    service._mirror_remote_results = project
    return service, account, session, state, scope


def current(service, account, session, scope):
    return private_inventory_binding_current(service, account, session, scope=scope, epoch=3)


class PrivateInventoryBindingTests(unittest.TestCase):
    def test_all_provider_names_share_one_guard_without_selected_profile(self):
        for provider in ('fixture-unknown', 'kobo', 'numilog', 'paris', 'bnf-pret'):
            with self.subTest(provider=provider):
                service, account, session, _, scope = rig(provider)
                self.assertTrue(current(service, account, session, scope))

    def test_empty_primary_account_is_exact_and_does_not_match_a_sibling(self):
        service, account, session, _, scope = rig(account_id='')
        self.assertTrue(current(service, account, session, scope))
        session.accounts = [NS(**dict(vars(account), unique_id='other'))]
        self.assertFalse(current(service, account, session, scope))

    def test_v1_requires_one_account_and_multiaccount_requires_explicit_projection(self):
        service, account, session, _, scope = rig()
        sibling = NS(**dict(vars(account), unique_id='second'))
        service.accounts.append(sibling)
        self.assertFalse(current(service, account, session, scope))
        session.accounts = [account, sibling]
        self.assertTrue(current(service, account, session, scope))
        session.accounts.append(copy(account))
        self.assertFalse(current(service, account, session, scope))

    def test_shared_credential_cannot_cross_profiles(self):
        service, account, session, _, scope = rig()
        service.accounts.append(NS(**dict(vars(account), identity_id='bob', unique_id='second')))
        session.accounts = [account]
        self.assertFalse(current(service, account, session, scope))

    def test_stale_or_ambiguous_context_never_passes(self):
        for mutation in ('epoch', 'account', 'session', 'closed', 'duplicate_session', 'owner',
                         'profile', 'disabled', 'connection', 'upstream'):
            with self.subTest(mutation=mutation):
                service, account, session, state, scope = rig()
                if mutation == 'epoch': service._source_context_epoch += 1
                elif mutation == 'account': account.unique_id = 'new-account'
                elif mutation == 'session': service._sessions[(scope[1], scope[2])] = copy(session)
                elif mutation == 'closed': session._closed = True
                elif mutation == 'duplicate_session': service._sessions['old'] = copy(session)
                elif mutation == 'owner': state.owner = 'bob'
                elif mutation == 'profile': state.profile = False
                elif mutation == 'disabled': state.enabled = False
                elif mutation == 'connection': state.connections[0]['enabled'] = False
                else: session.accounts = []
                self.assertFalse(current(service, account, session, scope))

    def test_unknown_provider_private_inventory_uses_exact_original_profile(self):
        service, account, _, _, _ = rig()
        result = _synchronize_account_source_impl(service, account)
        self.assertEqual(result['library'], 1)
        self.assertEqual(service.reads, [1])
        self.assertEqual(service.projections, [(1, 'alice')])

    def test_rebinding_during_read_cannot_project_notices(self):
        for mutation in ('epoch', 'account', 'session', 'owner', 'disabled'):
            with self.subTest(mutation=mutation):
                service, account, session, state, scope = rig()
                def library():
                    service.reads.append(1)
                    if mutation == 'epoch': service._source_context_epoch += 1
                    elif mutation == 'account': account.unique_id = 'new-account'
                    elif mutation == 'session': service._sessions[(scope[1],scope[2])] = copy(session)
                    elif mutation == 'owner': state.owner = 'bob'
                    else: state.enabled = False
                    return [object()]
                session.library = library
                with self.assertRaises(ProviderConfigurationError):
                    _synchronize_account_source_impl(service, account)
                self.assertEqual(service.reads, [1])
                self.assertEqual(service.projections, [])

    def test_unbound_private_source_never_calls_provider(self):
        service, account, _, state, _ = rig()
        state.owner = 'bob'
        with self.assertRaises(ProviderConfigurationError):
            _synchronize_account_source_impl(service, account)
        self.assertEqual(service.reads, [])
        self.assertEqual(service.projections, [])


def test_real_sqlite_manual_inventory_admission_is_provider_neutral(tmp_path):
    from cifereca.models import Account
    from tests.test_sync_state_contract import _service

    for provider in ('fixture-unknown', 'kobo', 'numilog'):
        directory = tmp_path / provider
        directory.mkdir()
        service = _service(directory)
        try:
            account = Account(identity_id='default', provider_id=provider,
                              credential_id='synthetic-connection', unique_id='synthetic-buyer')
            service.accounts.append(account)
            service.storage.provider_connection_put(dict(
                identity_id='default', provider_id=provider,
                credential_id=account.credential_id, enabled=True,
            ))
            calls = []
            session = NS(provider_id=provider, credential_id=account.credential_id,
                         connected=True, accounts=[account],
                         capabilities=NS(loans=False, personal_library=True),
                         library=lambda: calls.append(1) or [])
            service._sessions[service._session_key(provider, account.credential_id)] = session
            service.current_identity = service.identity_by_id('alex')
            result = service._synchronize_account_source_impl(account)
            assert result['library'] == 0
            assert calls == [1]
            assert service.storage.catalog_entries() == []
            assert service.storage.entitlements() == []
            assert service.storage.acquisitions() == []
            assert service.storage.backup_list() == []
        finally:
            service.close()
