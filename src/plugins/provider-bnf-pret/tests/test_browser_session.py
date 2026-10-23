"""Browser lifecycle contract; fake minimal Playwright, not Chromium E2E."""
import importlib.util
from pathlib import Path
import sys
import shutil
import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    import cifereca_bnf_pret_browser as browser
finally:
    sys.path.pop(0)

LOANS = ('<section class="active-loans"><div class="js-loan">'
         '<a href="/resources/synthetic-book"></a>'
         '<a href="/loans/synthetic-loan"></a></div></section>')


class FakePage:
    def __init__(self):
        self.url = browser.ORIGIN + '/'
        self.main_frame = object()
        self.status = 200
        self.html = LOANS
        self.calls = []
        self.events = {}
        self.evaluate_hook = None
        self.redirect = None

    def on(self, name, callback):
        self.events[name] = callback

    def goto(self, url, **kwargs):
        self.calls.append(('goto', url, kwargs))
        self.url = self.redirect or url
        self.events['framenavigated'](self.main_frame)
        return SimpleNamespace(status=self.status)

    def evaluate(self, script, args):
        self.calls.append(('evaluate', script, args))
        result = dict(code='ok', url=self.url, html=self.html)
        if self.evaluate_hook:
            return self.evaluate_hook(result)
        return result

    def wait_for_timeout(self, value):
        self.calls.append(('pump', value))


class FakeRuntime:
    def __init__(self, fail_at=None):
        self.fail_at = fail_at
        self.page = FakePage()
        self.calls = []
        self.chromium = self

    def start(self):
        self.calls.append(('start',))
        return self

    def launch(self, **kwargs):
        self.calls.append(('launch', kwargs))
        if self.fail_at == 'launch':
            raise RuntimeError('synthetic-private-browser-message')
        return self

    def new_context(self, **kwargs):
        self.calls.append(('context', kwargs))
        if self.fail_at == 'context':
            raise RuntimeError('synthetic-private-browser-message')
        return self

    def new_page(self):
        return self.page

    def route(self, pattern, callback):
        self.calls.append(('route', pattern))
        self.route_callback = callback

    def route_web_socket(self, pattern, callback):
        self.calls.append(('websocket', pattern))
        self.socket_callback = callback

    def close(self):
        self.calls.append(('close',))

    def stop(self):
        self.calls.append(('stop',))


class FakeRoute:
    def __init__(self, url, method='GET', document=True, resource_type='stylesheet'):
        self.request = SimpleNamespace(url=url, method=method, is_navigation_request=lambda: document,
                                       resource_type=resource_type)
        self.outcome = None

    def continue_(self):
        self.outcome = 'continued'

    def abort(self, reason):
        self.outcome = 'blocked'


class BrowserSessionTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.session = browser.ReaderBrowserSession('identity-a', 'account-a', factory=lambda: self.runtime)
        self.addCleanup(self.session.close)

    def ready(self):
        self.session.open_login(consent=True)
        return self.session.finish_login(account_confirmed=True)

    def test_import_and_constructor_do_not_launch(self):
        self.assertEqual(self.runtime.calls, [])

    def test_explicit_consent_required(self):
        for consent in (False, None, 1, 'yes'):
            with self.assertRaisesRegex(browser.BrowserSessionError, '^consent_required$'):
                self.session.open_login(consent=consent)
        self.assertEqual(self.runtime.calls, [])

    def test_ephemeral_context_and_visible_browser(self):
        self.session.open_login(consent=True)
        self.assertIn(('launch', {'headless': False}), self.runtime.calls)
        self.assertIn(('context', {'accept_downloads': False, 'service_workers': 'block'}), self.runtime.calls)
        self.assertFalse(any(x[0] == 'evaluate' for x in self.runtime.page.calls))

    def test_account_confirmation_before_reading(self):
        self.session.open_login(consent=True)
        with self.assertRaisesRegex(browser.BrowserSessionError, '^account_confirmation_required$'):
            self.session.finish_login()
        self.assertFalse(any(x[0] == 'evaluate' for x in self.runtime.page.calls))

    def test_confirmed_login_reads_activity(self):
        result = self.ready()
        self.assertEqual(result.scope, self.session.scope)
        self.assertEqual(len(result.loans), 1)
        self.assertIn(('route', '**/*'), self.runtime.calls)
        self.assertIn(('websocket', '**/*'), self.runtime.calls)

    def test_scope_cannot_be_reassigned(self):
        with self.assertRaises(AttributeError):
            self.session.scope = browser.ProfileScope('b', 'b', 'b')

    def test_contexts_and_epochs_are_distinct(self):
        other_runtime = FakeRuntime()
        other = browser.ReaderBrowserSession('identity-a', 'account-a', factory=lambda: other_runtime)
        self.addCleanup(other.close)
        self.assertNotEqual(self.session.scope, other.scope)
        self.ready()
        other.open_login(consent=True)
        other.finish_login(account_confirmed=True)
        before = len(self.runtime.page.calls)
        with self.assertRaisesRegex(browser.BrowserSessionError, '^scope_mismatch$'):
            self.session.loans(expected=other.scope)
        self.assertEqual(len(self.runtime.page.calls), before)

    def test_non_owner_thread_cannot_access_browser(self):
        errors = []
        def run():
            try:
                self.session.open_login(consent=True)
            except browser.BrowserSessionError as exc:
                errors.append(str(exc))
        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
        self.assertEqual(errors, ['wrong_thread'])
        self.assertEqual(self.runtime.calls, [])

    def test_read_before_login_and_after_close_refused(self):
        for _ in range(2):
            with self.assertRaisesRegex(browser.BrowserSessionError, '^invalid_session_state$'):
                self.session.loans(expected=self.session.scope)
            self.session.close()

    def test_closed_session_does_not_reopen(self):
        self.ready()
        self.session.close()
        with self.assertRaisesRegex(browser.BrowserSessionError, '^invalid_session_state$'):
            self.session.open_login(consent=True)
        self.assertIsNone(self.session._page)
        self.assertEqual(self.runtime.calls[-3:], [('close',), ('close',), ('stop',)])

    def test_http_failures_do_not_extract_html(self):
        self.ready()
        for status, expected in ((401, 'auth_required'), (403, 'access_refused'),
                                  (429, 'rate_limited'), (500, 'http_error'), (None, 'http_error')):
            with self.subTest(status=status):
                before = sum(c[0] == 'evaluate' for c in self.runtime.page.calls)
                self.runtime.page.status = status
                with self.assertRaisesRegex(browser.BrowserSessionError, '^' + expected + '$'):
                    self.session.loans(expected=self.session.scope)
                self.assertEqual(before, sum(c[0] == 'evaluate' for c in self.runtime.page.calls))

    def test_redirected_login_fails_closed_and_cleans_up(self):
        self.session.open_login(consent=True)
        self.runtime.page.redirect = 'https://idp.example.invalid/login'
        with self.assertRaisesRegex(browser.BrowserSessionError, '^login_not_verified$'):
            self.session.finish_login(account_confirmed=True)
        self.assertEqual(self.session._mode, 'closed')

    def test_browser_errors_do_not_escape(self):
        for fail_at in ('launch', 'context'):
            runtime = FakeRuntime(fail_at)
            session = browser.ReaderBrowserSession('a', 'a', factory=lambda: runtime)
            with self.assertRaisesRegex(browser.BrowserSessionError, '^browser_unavailable$') as ctx:
                session.open_login(consent=True)
            self.assertNotIn('private', str(ctx.exception))
            self.assertEqual(runtime.calls[-1], ('stop',))

    def test_detail_uses_only_resource_get(self):
        self.ready()
        self.runtime.page.html = ('<article itemscope itemtype="https://schema.org/Book">'
                                  '<h1 itemprop="name">Fiction</h1></article>')
        result = self.session.detail('synthetic-book', expected=self.session.scope)
        self.assertEqual(result.records[0].title, 'Fiction')
        self.assertEqual(self.runtime.page.url, browser.ORIGIN + '/resources/synthetic-book')

    def test_invalid_resource_refused_before_navigation(self):
        self.ready()
        for rid in ('../loans/x', 'a?token=secret', 'a/b', '', 'a/'):
            count = len(self.runtime.page.calls)
            with self.assertRaisesRegex(browser.BrowserSessionError, '^invalid_resource$'):
                self.session.detail(rid, expected=self.session.scope)
            self.assertEqual(len(self.runtime.page.calls), count)

    def test_suggestions_read_current_page_without_guessing_route(self):
        self.ready()
        self.runtime.page.url = browser.ORIGIN + '/results?q=synthetic'
        self.runtime.page.html = ('<ul class="search-results__list"><li class="search-resource-result">'
            '<a href="/resources/synthetic">Fiction</a></li></ul>')
        before = sum(c[0] == 'goto' for c in self.runtime.page.calls)
        result = self.session.suggestions(expected=self.session.scope)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(before, sum(c[0] == 'goto' for c in self.runtime.page.calls))

    def test_navigation_during_extraction_discards_result(self):
        self.ready()
        def change(result):
            self.session._navigated(self.runtime.page.main_frame)
            return result
        self.runtime.page.evaluate_hook = change
        with self.assertRaisesRegex(browser.BrowserSessionError, '^page_changed$'):
            self.session.suggestions(expected=self.session.scope)

    def test_unrecognized_dom_safe_error(self):
        self.ready()
        self.runtime.page.evaluate_hook = lambda _: {'code': 'unrecognized_dom'}
        with self.assertRaisesRegex(browser.BrowserSessionError, '^unrecognized_dom$'):
            self.session.suggestions(expected=self.session.scope)

    def test_oversized_html_is_rechecked_on_python_boundary(self):
        self.ready()
        self.runtime.page.html = 'x' * (browser.MAX_HTML_BYTES + 1)
        with self.assertRaisesRegex(browser.WebParseError, '^html_limit$'):
            self.session.suggestions(expected=self.session.scope)

    def test_readonly_request_gate(self):
        self.ready()
        for url, method, document, outcome in (
            (browser.ORIGIN + '/resources/synthetic', 'GET', True, 'continued'),
            (browser.ORIGIN + '/results?q=synthetic', 'GET', True, 'continued'),
            (browser.ORIGIN + '/my_profile/activity', 'GET', True, 'continued'),
            ('https://cdn.example.invalid/style.css', 'GET', False, 'continued'),
            (browser.ORIGIN + '/resources/synthetic', 'POST', False, 'blocked'),
            (browser.ORIGIN + '/loans/synthetic?signature=fake', 'GET', False, 'blocked'),
            (browser.ORIGIN + '/v1/resources.json', 'GET', False, 'blocked'),
            (browser.ORIGIN + '/%6coans/synthetic.xml', 'GET', False, 'blocked'),
            (browser.ORIGIN + '/%256coans/synthetic.xml', 'GET', False, 'blocked'),
            (browser.ORIGIN + '/logout', 'GET', False, 'blocked'),
            (browser.ORIGIN + '/logout', 'GET', True, 'blocked'),
            ('https://idp.example.invalid/login', 'GET', True, 'blocked'),
            (browser.ORIGIN + '/resources/a?token=synthetic', 'GET', True, 'blocked'),
        ):
            with self.subTest(url=url, method=method):
                route = FakeRoute(url, method, document)
                self.runtime.route_callback(route)
                self.assertEqual(route.outcome, outcome)

    def test_websocket_closed(self):
        self.ready()
        calls = []
        self.runtime.socket_callback(SimpleNamespace(close=lambda: calls.append('close')))
        self.assertEqual(calls, ['close'])

    def test_cli_wait_keeps_browser_events_on_owner_thread(self):
        self.ready()
        released = threading.Event()
        owners = []
        def pump(value):
            owners.append(threading.get_ident())
            released.set()
        def answer(_prompt):
            self.assertTrue(released.wait(2))
            return ' q '
        self.runtime.page.wait_for_timeout = pump
        with patch('builtins.input', answer):
            self.assertEqual(browser._ask(self.session, 'synthetic prompt'), 'q')
        self.assertTrue(owners)
        self.assertTrue(all(owner == threading.get_ident() for owner in owners))

    def test_readonly_gate_blocks_unrecognized_xhr(self):
        self.ready()
        for url, expected in ((browser.ORIGIN + '/results?q=synthetic', 'continued'),
                              (browser.ORIGIN + '/logout', 'blocked'),
                              (browser.ORIGIN + '/v1', 'blocked')):
            route = FakeRoute(url, document=False, resource_type='xhr')
            self.runtime.route_callback(route)
            self.assertEqual(route.outcome, expected)

    def test_repr_has_no_profile_or_browser_values(self):
        self.assertNotIn('identity-a', repr(self.session))
        self.assertNotIn('account-a', repr(self.session))

    def test_reader_urls(self):
        for url in (browser.ORIGIN + '/results?q=x&q=y', browser.ORIGIN + '/results?email=x',
                    browser.ORIGIN + '/resources/a#private', browser.ORIGIN + '/my_profile/activity?x=y',
                    'https://pret.bnf.fr@evil.test/home', browser.ORIGIN + '/v1/resources.json'):
            self.assertFalse(browser._reader_url(url))

    @unittest.skipUnless(shutil.which('node'), 'Node absent: JS unit contract unavailable')
    def test_production_js_extraction_with_synthetic_dom(self):
        result = subprocess.run(['node', str(ROOT / 'tests/extract_dom_contract.js')],
                                input=browser.EXTRACT_DOM, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, 'JavaScript extraction contract failed')
        self.assertEqual(result.stdout.strip(), 'extraction_js_contract_ok')


if __name__ == '__main__':
    unittest.main()
