"""Single browser owner, cancellation and private result scope contracts."""
from pathlib import Path
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    import cifereca_bnf_pret_worker as worker_module
    from cifereca_bnf_pret_web import ProfileScope
finally:
    sys.path.pop(0)

Error = worker_module.BrowserSessionError


class Control:
    def __init__(self):
        self.cancel = threading.Event()

    def cancelled(self):
        return self.cancel.is_set()


class FakeReader:
    def __init__(self, identity, account):
        self.scope = ProfileScope(identity, account, str(id(self)))
        self.owner = threading.get_ident()
        self.mode = 'new'
        self.calls = []
        self.pump_release = threading.Event()
        self.pump_release.set()
        self.read_entered = threading.Event()
        self.read_release = threading.Event()
        self.read_release.set()
        self.read_error = None

    def call(self, name):
        assert threading.get_ident() == self.owner
        self.calls.append(name)

    def open_login(self, *, consent):
        self.call('open')
        assert consent is True
        self.mode = 'login'

    def finish_login(self, *, account_confirmed):
        self.call('confirm')
        assert account_confirmed is True
        self.mode = 'readonly'
        return ('private-login-snapshot', self.scope)

    def loans(self, *, expected):
        self.call('loans')
        if self.mode != 'readonly':
            raise Error('invalid_session_state')
        assert expected == self.scope
        self.read_entered.set()
        assert self.read_release.wait(2)
        if self.read_error:
            raise self.read_error
        return ('private-loan-snapshot', self.scope)

    def suggestions(self, *, expected):
        self.call('suggestions')
        return ('private-suggestions', expected)

    def search(self, query, *, expected):
        self.call('search')
        assert expected == self.scope
        return ('private-search', query, expected)

    def current_detail(self, *, expected):
        self.call('visible_detail')
        return ('private-detail-visible', expected)

    def detail(self, resource_id, *, expected):
        self.call('detail')
        return (resource_id, expected)

    def pump(self):
        self.call('pump')
        assert self.pump_release.wait(2)
        time.sleep(0.001)

    def close(self):
        self.call('close')
        self.mode = 'closed'


class ReaderWorkerTests(unittest.TestCase):
    def setUp(self):
        self.sessions = []
        def factory(identity, account):
            session = FakeReader(identity, account)
            self.sessions.append(session)
            return session
        self.worker = worker_module.ReaderWorker('identity-a', 'account-a', session_factory=factory)
        self.control = Control()
        self.outcomes = []
        self.errors = []
        self.thread = None
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.worker.stop()
        for session in self.sessions:
            session.pump_release.set()
            session.read_release.set()
        if self.thread:
            self.thread.join(3)
            self.assertFalse(self.thread.is_alive(), 'worker did not terminate')

    def start(self):
        def run():
            try:
                self.outcomes.append(self.worker.run(self.control, consent=True))
            except Exception as exc:
                self.errors.append(str(exc))
        self.thread = threading.Thread(target=run)
        self.thread.start()
        deadline = time.monotonic() + 2
        while self.worker.state not in {'login', 'closed'} and time.monotonic() < deadline:
            time.sleep(.001)
        self.assertEqual(self.worker.state, 'login')
        return self.worker.scope

    def confirm(self, scope):
        return self.worker.submit('confirm_account', expected=scope, account_confirmed=True).result(2)

    def test_constructor_does_not_create_browser(self):
        self.assertEqual(self.sessions, [])
        self.assertIsNone(self.worker.scope)
        self.assertEqual(self.worker.state, 'new')

    def test_consent_refused_without_browser(self):
        with self.assertRaisesRegex(Error, '^consent_required$'):
            self.worker.run(self.control)
        self.assertEqual(self.sessions, [])
        self.assertEqual(self.worker.state, 'closed')

    def test_stop_before_task_starts(self):
        self.worker.stop()
        self.assertEqual(self.worker.run(self.control, consent=True), 'cancelled')
        self.assertEqual(self.sessions, [])

    def test_pre_cancelled_task_does_not_open(self):
        self.control.cancel.set()
        self.assertEqual(self.worker.run(self.control, consent=True), 'cancelled')
        self.assertEqual(self.sessions, [])

    def test_all_browser_operations_share_owner_thread(self):
        scope = self.start()
        self.confirm(scope)
        self.worker.submit('loans', expected=scope).result(2)
        self.worker.submit('suggestions', expected=scope).result(2)
        self.assertEqual(
            self.worker.submit('search', expected=scope, search_query='Synthetic title').result(2),
            ('private-search', 'Synthetic title', scope),
        )
        self.worker.submit('detail', expected=scope, resource_id='synthetic').result(2)
        self.assertEqual(self.worker.submit('visible_detail', expected=scope).result(2),
                         ('private-detail-visible', scope))
        self.worker.stop()
        self.thread.join(2)
        self.assertEqual(self.errors, [])
        self.assertNotEqual(self.sessions[0].owner, threading.get_ident())
        self.assertEqual(self.sessions[0].calls[-1], 'close')
        self.assertEqual(self.outcomes, ['cancelled'])

    def test_human_account_confirmation_required(self):
        scope = self.start()
        with self.assertRaisesRegex(Error, '^account_confirmation_required$'):
            self.worker.submit('confirm_account', expected=scope)
        self.assertNotIn('confirm', self.sessions[0].calls)

    def test_scope_mismatch_refused_before_queue(self):
        self.start()
        for scope in (ProfileScope('b', 'account-a', 'epoch'), None, 'identity-a'):
            with self.assertRaisesRegex(Error, '^scope_mismatch$'):
                self.worker.submit('loans', expected=scope)
        self.assertNotIn('loans', self.sessions[0].calls)

    def test_invalid_operations_cannot_mutate(self):
        scope = self.start()
        for name in ('borrow', 'download', 'return', 'open_login', lambda: None):
            with self.assertRaisesRegex(Error, '^unsupported_reader_command$'):
                self.worker.submit(name, expected=scope)

    def test_search_query_never_leaks_and_mutating_routes_remain_forbidden(self):
        scope = self.start()
        for search_query in (None, '', ' ', 'a', 'secret\ntrace', 'x' * 129, 4):
            with self.assertRaisesRegex(Error, '^invalid_search$'):
                self.worker.submit('search', expected=scope, search_query=search_query)
        with self.assertRaisesRegex(Error, '^invalid_search$'):
            self.worker.submit('loans', expected=scope, search_query='private')
        future = self.worker.submit('search', expected=scope, search_query='private-search-term')
        self.assertNotIn('private-search-term', repr(self.worker))
        self.assertNotIn('private-search-term', repr(worker_module._Command(
            'search', scope, future, query='private-search-term')))
        self.assertEqual(future.result(2)[0], 'private-search')

    def test_cancelled_request_not_executed(self):
        scope = self.start()
        self.sessions[0].pump_release.clear()
        future = self.worker.submit('suggestions', expected=scope)
        self.assertTrue(future.cancel())
        self.sessions[0].pump_release.set()
        self.confirm(scope)
        self.assertNotIn('suggestions', self.sessions[0].calls)

    def test_stop_discards_inflight_result(self):
        scope = self.start()
        self.confirm(scope)
        self.sessions[0].read_release.clear()
        future = self.worker.submit('loans', expected=scope)
        self.assertTrue(self.sessions[0].read_entered.wait(2))
        self.worker.stop()  # UI identity change / teardown
        self.assertIsNone(self.worker.scope)
        self.sessions[0].read_release.set()
        with self.assertRaisesRegex(Error, '^session_closed$'):
            future.result(2)

    def test_control_cancellation_discards_inflight_result(self):
        scope = self.start()
        self.confirm(scope)
        self.sessions[0].read_release.clear()
        future = self.worker.submit('loans', expected=scope)
        self.assertTrue(self.sessions[0].read_entered.wait(2))
        self.control.cancel.set()
        self.sessions[0].read_release.set()
        with self.assertRaisesRegex(Error, '^session_closed$'):
            future.result(2)
        self.thread.join(2)
        self.assertIsNone(self.worker.scope)

    def test_stop_resolves_pending_commands(self):
        scope = self.start()
        self.sessions[0].pump_release.clear()
        future = self.worker.submit('suggestions', expected=scope)
        self.worker.stop()
        self.sessions[0].pump_release.set()
        with self.assertRaisesRegex(Error, '^session_closed$'):
            future.result(2)

    def test_queue_is_bounded(self):
        scope = self.start()
        self.sessions[0].pump_release.clear()
        # The consumer may have dequeued one item before entering pump.
        rejected = False
        for _ in range(10):
            try:
                self.worker.submit('suggestions', expected=scope)
            except Error as exc:
                self.assertEqual(str(exc), 'reader_busy')
                rejected = True
                break
        self.assertTrue(rejected)

    def test_arbitrary_browser_exception_is_not_exposed(self):
        scope = self.start()
        self.confirm(scope)
        self.sessions[0].read_error = RuntimeError('synthetic-private-url-and-account')
        with self.assertRaisesRegex(Error, '^reader_operation_failed$'):
            self.worker.submit('loans', expected=scope).result(2)

    def test_known_safe_failure_keeps_code(self):
        scope = self.start()
        self.confirm(scope)
        self.sessions[0].read_error = Error('rate_limited')
        with self.assertRaisesRegex(Error, '^rate_limited$'):
            self.worker.submit('loans', expected=scope).result(2)

    def test_repr_and_broker_result_do_not_contain_private_data(self):
        scope = self.start()
        self.confirm(scope)
        self.worker.stop()
        self.thread.join(2)
        self.assertNotIn('identity-a', repr(self.worker))
        self.assertNotIn('private-', repr(self.outcomes))
        self.assertEqual(self.outcomes, ['cancelled'])


if __name__ == '__main__':
    unittest.main()
