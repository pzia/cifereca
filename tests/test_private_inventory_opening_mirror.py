"""Opening metadata uses the same private binding as explicit source Sync."""
from copy import copy
from types import SimpleNamespace as NS
import unittest

from cifereca.plugin_api import Cancelled
from cifereca.services import LibraryService
from tests.test_private_inventory_binding import rig


class OpeningPrivateMirrorTests(unittest.TestCase):
    def test_unknown_source_keeps_own_profile_without_ui_fallback(self):
        service, _, _, _, _ = rig()
        result = LibraryService.mirror_personal_libraries(service)
        self.assertEqual(result, {'fixture-unknown:connection-a': 1})
        self.assertEqual((result.blocked_sources, result.failed_sources), (0, 0))
        self.assertEqual(service.projections, [(1, 'alice')])

    def test_changed_binding_discards_rows_and_is_not_provider_failure(self):
        for mutation in ('epoch', 'account', 'owner', 'session', 'disconnected'):
            with self.subTest(mutation=mutation):
                service, account, session, state, scope = rig()
                def library():
                    service.reads.append(1)
                    if mutation == 'epoch': service._source_context_epoch += 1
                    elif mutation == 'account': account.unique_id = 'new-buyer'
                    elif mutation == 'owner': state.owner = 'bob'
                    elif mutation == 'session': service._sessions[(scope[1], scope[2])] = copy(session)
                    else: session.connected = False
                    return [object()]
                session.library = library
                result = LibraryService.mirror_personal_libraries(service)
                self.assertEqual(result, {})
                self.assertEqual((result.blocked_sources, result.failed_sources), (1, 0))
                self.assertEqual(service.projections, [])
                self.assertEqual(service.reads, [1])

    def test_ambiguous_account_never_chooses_first_borrower(self):
        service, account, session, _, _ = rig()
        sibling = NS(**dict(vars(account), unique_id='second'))
        service.accounts.append(sibling)
        session.accounts = [account, sibling]
        result = LibraryService.mirror_personal_libraries(service)
        self.assertEqual(result.blocked_sources, 1)
        self.assertEqual(service.reads, [])

    def test_duplicate_session_route_is_reported_once_without_read(self):
        service, _, session, _, _ = rig()
        service._sessions['alias'] = copy(session)
        result = LibraryService.mirror_personal_libraries(service)
        self.assertEqual(result.blocked_sources, 1)
        self.assertEqual(service.reads, [])

    def test_new_session_during_read_does_not_change_current_batch(self):
        service, _, session, _, _ = rig()
        original = session.library
        def library():
            service._sessions['added'] = NS(provider_id='another', credential_id='another',
                                          connected=True, capabilities=session.capabilities,
                                          library=lambda: self.fail('not enrolled in this batch'))
            return original()
        session.library = library
        result = LibraryService.mirror_personal_libraries(service)
        self.assertEqual(result, {'fixture-unknown:connection-a': 1})
        self.assertEqual(service.reads, [1])

    def test_cancellation_propagates_without_projection_or_error_conversion(self):
        service, _, session, _, _ = rig()
        cancelled = Cancelled('synthetic cancellation')
        def library():
            raise cancelled
        session.library = library
        with self.assertRaises(Cancelled) as caught:
            LibraryService.mirror_personal_libraries(service)
        self.assertIs(caught.exception, cancelled)
        self.assertEqual(service.projections, [])

    def test_disabled_source_does_not_request_inventory(self):
        service, _, _, state, _ = rig()
        state.enabled = False
        result = LibraryService.mirror_personal_libraries(service)
        self.assertEqual(result, {})
        self.assertEqual((result.blocked_sources, result.failed_sources), (0, 0))
        self.assertEqual(service.reads, [])
