"""Synthetic HTTPX contracts; no live account or private capture."""
import importlib
import json
from pathlib import Path
import sys
import unittest
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1]))
from cifereca_bnf_pret_http import ReaderHTTPClient, ReaderHTTPError, ACTIVITY, allowed_url
from cifereca_bnf_pret import BnfProviderSession, PLUGIN
from cifereca.plugin_api import ProviderConfigurationError

ACT = '<section class="active-loans"><div class="js-loan"><a href="/resources/book-a">Titre synthétique</a><a href="/loans/loan-a">Fichier</a></div></section>'
LOGIN = '<form method="post" action="login"><input name="username"><input type="password" name="password"><input type="hidden" name="execution" value="synthetic-flow"><input type="hidden" name="_eventId" value="submit"></form>'
SAML = '<form method="post" action="https://pret.bnf.fr/sso/saml/consume"><input type="hidden" name="SAMLResponse" value="synthetic-not-an-assertion"></form>'


class HTTPProviderTests(unittest.TestCase):
    def make(self, activity=ACT):
        calls = []
        state = {'activity': activity, 'login': LOGIN}
        def handler(r):
            calls.append((r.method, r.url.host, r.url.path))
            if r.url == ACTIVITY:
                if r.headers.get('cookie') == 'reader=synthetic':
                    return httpx.Response(200, text=state['activity'], headers={'content-type':'text/html'})
                return httpx.Response(302, headers={'location':'https://idppub.bnf.fr/idp/profile/SAML2/Redirect/SSO'})
            if r.url.host == 'idppub.bnf.fr':
                return httpx.Response(302, headers={'location':'https://authentification.bnf.fr/login?service=synthetic'})
            if r.url.host == 'authentification.bnf.fr':
                if r.method == 'GET':
                    text = state['login']
                else:
                    self.assertIn(b'username=reader', r.content)
                    self.assertIn(b'execution=synthetic-flow', r.content)
                    text = SAML
                return httpx.Response(200, text=text, headers={'content-type':'text/html'})
            if r.url.path == '/sso/saml/consume':
                return httpx.Response(302, headers={'location':ACTIVITY,'set-cookie':'reader=synthetic; Path=/; Secure'})
            self.fail('unexpected request')
        client = ReaderHTTPClient(transport=httpx.MockTransport(handler))
        session = BnfProviderSession(client=client, credential_id='credential-a', label='BnF',
                                     credentials={'username':'reader','password':'synthetic-password'}, options={})
        return session, calls, state

    def test_login_and_loans_through_real_provider_contract(self):
        s, calls, state = self.make()
        a, = s.connect()
        a.identity_id = 'profile-a'
        rows = s.loans(a)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row.title,row.record_id,row.id),('Titre synthétique','book-a','loan-a'))
        self.assertEqual((row.source_provider,row.source_credential,row.source_account_id,row.identity_id),('bnf-pret','credential-a','credential-a','profile-a'))
        self.assertIsNone(row.due_at)
        self.assertIsNone(row.ebook_url)
        self.assertTrue(s.last_loan_errors)
        state['activity'] = '<section class="active-loans"></section>'
        self.assertEqual(s.loans(a), [])
        self.assertTrue(s.last_loan_errors)
        self.assertFalse(any('/loans/' in p or '/v1' in p for _,_,p in calls))
        s.close()
        with self.assertRaises(ProviderConfigurationError): s.loans(a)

    def test_two_sessions_and_wrong_account(self):
        first, _, _ = self.make()
        second, _, _ = self.make()
        a, = first.connect()
        with self.assertRaises(ProviderConfigurationError): second.loans(a)
        b, = second.connect()
        with self.assertRaises(ProviderConfigurationError): first.loans(b)
        first.close()
        self.assertEqual(len(second.loans(b)),1)
        second.close()

    def test_changed_html_does_not_return_empty_success(self):
        s, _, state = self.make()
        a, = s.connect()
        state['activity'] = '<html>Session expirée</html>'
        with self.assertRaisesRegex(ProviderConfigurationError,'unrecognized_loans'): s.loans(a)
        self.assertFalse(s.connected)
        s.close()

    def test_bad_login_and_interactive_requirement(self):
        for page, code in [('<html>2FA</html>','auth_interactive_required'), (LOGIN,'authentication_failed')]:
            s, _, state = self.make()
            # Force the login POST to return the supplied page instead of SAML.
            base = s.client.request
            def request(method,url,**kw):
                if method == 'POST': return 'https://authentification.bnf.fr/login', page
                return base(method,url,**kw)
            s.client.request = request
            with self.assertRaisesRegex(ProviderConfigurationError,code): s.connect()
            self.assertFalse(s.connected)
            s.close()

    def test_no_credential_post_to_foreign_origin(self):
        s,calls,state = self.make()
        state['login'] = LOGIN.replace('action="login"','action="https://evil.test/login"')
        with self.assertRaisesRegex(ProviderConfigurationError,'url_refused'): s.connect()
        self.assertFalse(any(method=='POST' for method,_,_ in calls))
        s.close()

    def test_read_requests_refuse_auth_redirects_and_mutations(self):
        s,calls,_ = self.make()
        with self.assertRaisesRegex(ReaderHTTPError,'url_refused'): s.client.activity()
        for url in ['http://pret.bnf.fr/my_profile/activity','https://pret.bnf.fr.evil.test/my_profile/activity',
                    'https://pret.bnf.fr/loans/a','https://pret.bnf.fr/v1/loans',
                    'https://pret.bnf.fr:444/my_profile/activity','https://pret.bnf.fr/my_profile/activity#x']:
            with self.assertRaises(ReaderHTTPError): allowed_url(url)
        with self.assertRaisesRegex(ReaderHTTPError,'mutation_refused'):
            s.client.request('POST',ACTIVITY)
        s.close()

    def test_http_failures_limits_and_no_retry(self):
        for status,body,ctype,code in [(403,b'', 'text/html','access_denied'),(429,b'','text/html','rate_limited'),
            (200,b'x'*(512*1024+1),'text/html','html_limit'),(200,b'fake','application/xml','unexpected_content')]:
            calls=[]
            def handler(r):
                calls.append(r)
                return httpx.Response(status,content=body,headers={'content-type':ctype})
            c=ReaderHTTPClient(transport=httpx.MockTransport(handler))
            with self.assertRaisesRegex(ReaderHTTPError,code): c.activity()
            self.assertEqual(len(calls),1)
            c.close()

    def test_post_redirect_is_never_replayed(self):
        calls = []
        def handler(r):
            calls.append(r)
            return httpx.Response(307, headers={'location':'https://authentification.bnf.fr/login'})
        c = ReaderHTTPClient(transport=httpx.MockTransport(handler))
        with self.assertRaisesRegex(ReaderHTTPError, 'post_redirect_refused'):
            c.request('POST', 'https://authentification.bnf.fr/login',
                      data={'password':'synthetic'}, authenticating=True)
        self.assertEqual(len(calls), 1)
        c.close()

    def test_close_invalidates_inflight_result(self):
        s, _, _ = self.make()
        account, = s.connect()
        def activity():
            s.close()
            return ACT
        s.client.activity = activity
        with self.assertRaisesRegex(ProviderConfigurationError, 'session_closed'):
            s.loans(account)
        self.assertFalse(s.connected)

    def test_entrypoint_is_provider_and_has_no_browser_prerequisite(self):
        m=json.loads((ROOT/'cifereca-plugin.json').read_text())
        self.assertEqual(m['plugins'][0]['id'],'provider.bnf-pret')
        self.assertEqual(m['plugins'][0]['system_requires'],[])
        self.assertFalse(PLUGIN.descriptor.capabilities.digital_download)
        self.assertFalse(PLUGIN.descriptor.capabilities.borrow)

if __name__ == '__main__': unittest.main()


def test_provider_loans_persist_through_core_without_absence_deletion(tmp_path):
    # Exercise the actual Core opening sync and SQLite with the actual provider.
    sys.path.insert(0, str(ROOT.parents[2]))
    from tests.test_sync_state_contract import _service
    case = HTTPProviderTests()
    session, calls, state = case.make()
    service = _service(tmp_path)
    try:
        account, = session.connect()
        account.identity_id = 'default'
        service.accounts = [account]
        service._sessions[service._session_key('bnf-pret', 'credential-a')] = session
        summary = service.opening_sync()
        rights = service.storage.entitlements()
        assert len(rights) == 1
        assert rights[0]['entitlement_type'] == 'loan'
        assert rights[0]['state'] == 'active'
        assert summary['identities'][0]['status'] == 'partial'
        state['activity'] = '<section class="active-loans"></section>'
        service.opening_sync()
        assert service.storage.entitlements()[0]['state'] == 'active'
        assert not any('/loans/' in path for _,_,path in calls)
    finally:
        service.close()


def test_official_package_install_activation_and_disable_without_network(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT.parents[2] / 'scripts'))
    from plugin_packaging import build_plugin_zip
    from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
    def forbidden(*args, **kwargs):
        raise AssertionError('activation must not connect')
    monkeypatch.setattr(httpx.Client, 'request', forbidden)
    monkeypatch.setattr(httpx.Client, 'send', forbidden)
    archive = build_plugin_zip(ROOT, tmp_path / 'dist')
    manager = PluginManager(package_store=PackageStore(tmp_path / 'packages'),
                            state_store=PluginStateStore(tmp_path / 'state.json'))
    try:
        manager.install(archive)
        manager.set_enabled('provider.bnf-pret', True)
        assert manager.status('provider.bnf-pret').state == 'ready'
        service = manager.service('provider.bnf-pret')
        assert service.descriptor.id == 'bnf-pret'
        manager.set_enabled('provider.bnf-pret', False)
        assert manager.status('provider.bnf-pret').state == 'disabled'
    finally:
        manager.close()
