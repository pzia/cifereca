"""Private refresh binding is identical for known and unknown providers."""
from copy import copy
from types import SimpleNamespace as NS
import unittest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.source_refresh_policy import (
    inventory_source_eligible, kobo_inventory_source_eligible,
    numilog_inventory_source_eligible,
)


PROVIDERS = ('fixture-unknown', 'kobo', 'numilog', 'paris', 'bnf-pret')


def rig(provider):
    account = NS(provider_id=provider, identity_id='alice',
                 credential_id='credential-a', unique_id='account-a')
    session = NS(provider_id=provider, credential_id=account.credential_id,
                 connected=True, capabilities=NS(personal_library=True, backup=False))
    connection = dict(provider_id=provider, credential_id=account.credential_id, enabled=True)
    state = NS(owner='alice', enabled=None, identity=True, source_enabled=True,
               connections=[connection], reads=[])
    def metadata_get(key):
        state.reads.append(key)
        return state.enabled
    storage = NS(metadata_get=metadata_get,
                 identity_for_connection=lambda p, c: state.owner,
                 provider_connections=lambda **kw: state.connections)
    service = NS(accounts=[account], _sessions={(provider, account.credential_id): session},
                 _session_key=lambda p, c: (p, c), storage=storage,
                 identity_by_id=lambda identity: NS(identity_id=identity) if state.identity else None,
                 sync_source_enabled=lambda a: state.source_enabled,
                 current_identity=NS(identity_id='bob'))
    return service, account, session, state


class InventorySourceBindingTests(unittest.TestCase):
    def test_same_binding_for_every_provider_without_selected_identity_or_backup(self):
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                service, account, session, state = rig(provider)
                self.assertTrue(inventory_source_eligible(service, account))
                self.assertEqual(state.reads, ['sync.source.enabled.' +
                                               SynchronizationBrokerImpl._source_id(account)])
                self.assertFalse(session.capabilities.backup)

    def test_empty_primary_account_id_is_exact_and_still_bound(self):
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                service, account, _, state = rig(provider)
                account.unique_id = ''
                self.assertTrue(inventory_source_eligible(service, account))
                expected_key = 'sync.source.enabled.' + SynchronizationBrokerImpl._source_id(account)
                self.assertEqual(state.reads, [expected_key])
                service.accounts.append(copy(account))
                self.assertFalse(inventory_source_eligible(service, account))
                service.accounts.pop()
                state.owner = 'bob'
                self.assertFalse(inventory_source_eligible(service, account))

    def test_disabled_corrupt_or_non_boolean_enablement_never_grants(self):
        for provider in PROVIDERS:
            for value in ('0', 'false', '', 'TRUE', 1, True, [], {}):
                with self.subTest(provider=provider, value=value):
                    service, account, _, state = rig(provider)
                    state.enabled = value
                    self.assertFalse(inventory_source_eligible(service, account))
            for value in (False, 1, 'true', None):
                with self.subTest(provider=provider, source_enabled=value):
                    service, account, _, state = rig(provider)
                    state.source_enabled = value
                    self.assertFalse(inventory_source_eligible(service, account))

    def test_explicit_inventory_aliases_remain_compatible(self):
        for provider in PROVIDERS:
            for value in ('1', 'true', 'yes', 'on'):
                with self.subTest(provider=provider, value=value):
                    service, account, _, state = rig(provider)
                    state.enabled = value
                    self.assertTrue(inventory_source_eligible(service, account))

    def test_duplicate_detached_or_rebound_account_never_grants(self):
        for provider in PROVIDERS:
            for mutation in ('duplicate', 'detached', 'rebound', 'profile_removed', 'connection_disabled',
                             'connection_missing', 'connection_duplicate', 'connection_wrong_provider'):
                with self.subTest(provider=provider, mutation=mutation):
                    service, account, _, state = rig(provider)
                    if mutation == 'duplicate':
                        duplicate = copy(account)
                        duplicate.identity_id = 'bob'
                        service.accounts.append(duplicate)
                    elif mutation == 'detached': service.accounts = [copy(account)]
                    elif mutation == 'rebound': state.owner = 'bob'
                    elif mutation == 'profile_removed': state.identity = False
                    elif mutation == 'connection_disabled': state.connections[0]['enabled'] = False
                    elif mutation == 'connection_missing': state.connections = []
                    elif mutation == 'connection_duplicate': state.connections.append(dict(state.connections[0]))
                    else: state.connections[0]['provider_id'] = 'foreign'
                    self.assertFalse(inventory_source_eligible(service, account))

    def test_missing_duplicate_or_wrong_session_never_grants(self):
        for provider in PROVIDERS:
            for mutation in ('missing', 'duplicate', 'provider', 'credential', 'disconnected',
                             'connected_non_bool', 'no_library', 'library_non_bool'):
                with self.subTest(provider=provider, mutation=mutation):
                    service, account, session, _ = rig(provider)
                    if mutation == 'missing': service._sessions = {}
                    elif mutation == 'duplicate': service._sessions['old'] = copy(session)
                    elif mutation == 'provider': session.provider_id = 'foreign'
                    elif mutation == 'credential': session.credential_id = 'foreign'
                    elif mutation == 'disconnected': session.connected = False
                    elif mutation == 'connected_non_bool': session.connected = 1
                    elif mutation == 'no_library': session.capabilities.personal_library = False
                    else: session.capabilities.personal_library = 1
                    self.assertFalse(inventory_source_eligible(service, account))

    def test_invalid_coordinates_never_grant_or_read_storage(self):
        for provider in PROVIDERS:
            for field in ('provider_id', 'identity_id', 'credential_id', 'unique_id'):
                invalid = (' ', None, True, 'broken\x00coordinate')
                if field != 'unique_id':
                    invalid = ('',) + invalid
                for value in invalid:
                    with self.subTest(provider=provider, field=field, value=value):
                        service, account, _, state = rig(provider)
                        setattr(account, field, value)
                        self.assertFalse(inventory_source_eligible(service, account))
                        self.assertEqual(state.reads, [])

    def test_storage_exception_denies_without_retry(self):
        service, account, _, state = rig('fixture-unknown')
        def fail(key):
            state.reads.append(key)
            raise RuntimeError('synthetic metadata failure')
        service.storage.metadata_get = fail
        self.assertFalse(inventory_source_eligible(service, account))
        self.assertEqual(len(state.reads), 1)

    def test_schedule_uses_sync_binding_for_closed_epoch_and_upstream_accounts(self):
        for provider in PROVIDERS:
            for mutation in ('closed', 'corrupt_epoch', 'upstream_mismatch', 'missing_route'):
                with self.subTest(provider=provider, mutation=mutation):
                    service, account, session, state = rig(provider)
                    if mutation == 'closed':
                        session._closed = True
                    elif mutation == 'corrupt_epoch':
                        service._source_context_epoch = 'invalid'
                    elif mutation == 'upstream_mismatch':
                        session.accounts = [NS(**dict(vars(account), unique_id='other'))]
                    else:
                        service._session_key = lambda p, c: ('foreign', p, c)
                    self.assertFalse(inventory_source_eligible(service, account))

    def test_compatibility_names_preserve_provider_scope(self):
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                service, account, _, _ = rig(provider)
                self.assertEqual(kobo_inventory_source_eligible(service, account), provider == 'kobo')
                self.assertEqual(numilog_inventory_source_eligible(service, account), provider == 'numilog')
