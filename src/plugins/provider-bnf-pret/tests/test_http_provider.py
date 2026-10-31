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

FAKE_SIGNED_HREF = ('/loans/loan-a?device_type=reader&amp;drm=acs4&amp;'
                    'nonce=syntheticNonce123&amp;timestamp=2026-10-26T00%3A00%3A00Z&amp;'
                    'token=0123456789abcdef0123456789abcdef01234567')
ACT = ('<section class="active-loans"><div class="js-loan">'
       '<a href="/resources/book-a">Titre synthétique</a>'
       '<a class="actions-list__link actions-list__sublink js-accordeon__item" '
       'aria-controls="loan-a-reader-acs" href="' + FAKE_SIGNED_HREF + '">Fichier</a>'
       '</div></section>')
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
        self.assertNotIn('last_loan_errors', vars(s))
        state['activity'] = '<section class="active-loans"></section>'
        self.assertEqual(s.loans(a), [])
        self.assertNotIn('last_loan_errors', vars(s))
        self.assertFalse(any('/loans/' in p or '/v1' in p for _,_,p in calls))
        s.close()
        with self.assertRaises(ProviderConfigurationError): s.loans(a)

    def test_typed_partial_inventory_replaces_mutable_v1_error_sidechannel(self):
        s, calls, state = self.make()
        try:
            account, = s.connect()
            account.identity_id = 'profile-a'
            prior = len(calls)
            positive = s.loan_inventory_snapshot(account)
            self.assertEqual(len(calls), prior + 1)
            self.assertEqual(positive.status, 'partial')
            self.assertEqual(positive.reason_code, 'inventory_not_exhaustive')
            self.assertEqual(len(positive.loans), 1)
            self.assertEqual(len(positive.branches), 1)
            self.assertEqual((positive.branches[0].upstream,
                              positive.branches[0].completeness),
                             ('bnf_html', 'partial'))
            self.assertNotIn('last_loan_errors', vars(s))

            # An empty observed page is NOT proof that an earlier right
            # ended. One typed read is the only authoritative envelope.
            state['activity'] = '<section class="active-loans"></section>'
            prior = len(calls)
            empty = s.loan_inventory_snapshot(account)
            self.assertEqual(len(calls), prior + 1)
            self.assertEqual(empty.loans, ())
            self.assertEqual(empty.status, 'partial')
            self.assertEqual(empty.reason_code, 'inventory_not_exhaustive')
            self.assertEqual(empty.branches[0].completeness, 'partial')
            self.assertNotIn('last_loan_errors', vars(s))
            self.assertFalse(any('/loans/' in p for _, _, p in calls))
        finally:
            s.close()

    def test_positive_loan_cover_reaches_core_without_extra_http(self):
        activity = ('<section class="active-loans">'
                    '<div class="js-loan">'
                    '<a href="/resources/book-a">Titre synthétique'
                    '<img src="/covers/book-a.jpg"></a>'
                    '<a href="/loans/loan-a">Jeton</a>'
                    '</div></section>')
        session, calls, _state = self.make(activity)
        try:
            account, = session.connect()
            account.identity_id = 'profile-a'
            rows = session.loans(account)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].thumbnail_url,
                             'https://pret.bnf.fr/covers/book-a.jpg')
            self.assertIsNone(rows[0].due_at)
            self.assertFalse(any('/covers/' in path or '/loans/' in path
                                 for _, _, path in calls))
        finally:
            session.close()

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

    def test_diagnostics_keep_public_code_and_original_provider_failure(self):
        from types import SimpleNamespace
        for error, expected in [('access_denied', 'access_denied'),
                                ('PRIVATE_RESPONSE_cookie=secret', 'bnf_reader_failed')]:
            s, _, _ = self.make()
            events = []
            s.diagnostics = SimpleNamespace(add=lambda *a, **kw: events.append(kw['detail']))
            def login(*args):
                raise ReaderHTTPError(error)
            s.client.login = login
            try:
                with self.assertRaises(ProviderConfigurationError) as captured:
                    s.connect()
                self.assertEqual(str(captured.exception), expected)
                self.assertEqual(len(events), 1)
                detail = events[0]
                self.assertEqual((detail['provider'], detail['phase'], detail['code']),
                                 ('bnf-pret', 'login', expected))
                self.assertEqual(detail['error_type'], 'ReaderHTTPError')
                self.assertEqual(detail['error_message'], expected)
                self.assertEqual(detail['error_traceback'], 'redacted')
                if error != expected:
                    self.assertNotIn(error, json.dumps(detail))
            finally:
                s.close()

    def test_missing_optional_diagnostics_still_logs_original_error_once(self):
        s, _, _ = self.make()
        s.diagnostics = None
        message = 'SYNTHETIC_HTML_FAILURE_original_reason'
        s.client.login = lambda *args: (_ for _ in ()).throw(ReaderHTTPError(message))
        try:
            with self.assertLogs('cifereca', level='ERROR') as logs:
                with self.assertRaises(ProviderConfigurationError) as captured:
                    s.connect()
            self.assertEqual(str(captured.exception), 'bnf_reader_failed')
            rows = [json.loads(row.split('DIAG_JSON ', 1)[1])
                    for row in logs.output if 'DIAG_JSON ' in row]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['detail']['error_message'], 'bnf_reader_failed')
            self.assertEqual(rows[0]['detail']['error_traceback'], 'redacted')
            self.assertNotIn(message, repr(logs.output))
            self.assertEqual(rows[0]['detail']['code'], 'bnf_reader_failed')
            self.assertEqual(rows[0]['detail']['phase'], 'login')
        finally:
            s.close()

    def test_diagnostic_failure_does_not_hide_provider_failure(self):
        from types import SimpleNamespace
        s, _, _ = self.make()
        def unavailable(*a, **kw):
            raise RuntimeError('synthetic diagnostic failure')
        s.diagnostics = SimpleNamespace(add=unavailable)
        s.client.login = lambda *a: (_ for _ in ()).throw(ReaderHTTPError('access_denied'))
        with self.assertRaisesRegex(ProviderConfigurationError, '^access_denied$'):
            s.connect()
        s.close()

    def test_entrypoint_is_provider_and_has_no_browser_prerequisite(self):
        m=json.loads((ROOT/'cifereca-plugin.json').read_text())
        self.assertEqual(m['plugins'][0]['id'],'provider.bnf-pret')
        self.assertEqual(m['plugins'][0]['system_requires'],[])
        self.assertFalse(PLUGIN.descriptor.capabilities.digital_download)
        self.assertFalse(PLUGIN.descriptor.capabilities.borrow)



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
    from cifereca.plugin_api import HTTPX_TRANSPORT
    def forbidden(*args, **kwargs):
        raise AssertionError('activation must not connect')
    monkeypatch.setattr(httpx.Client, 'request', forbidden)
    monkeypatch.setattr(httpx.Client, 'send', forbidden)
    archive = build_plugin_zip(ROOT, tmp_path / 'dist')
    manager = PluginManager(brokers={'httpx': HTTPX_TRANSPORT},
                            package_store=PackageStore(tmp_path / 'packages'),
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


def search_html(ids=('book-a',), following=''):
    cards = ''.join('<li class="search-resource-result">'
                    f'<a href="/resources/{rid}"><span itemprop="name">Titre {rid}</span></a>'
                    '<span itemprop="author">Auteur fictif</span>'
                    '<meta itemprop="publisher" content="Éditeur fictif">'
                    '<meta itemprop="isbn" content="9780000000002">'
                    '<meta itemprop="inLanguage" content="fr">'
                    '</li>' for rid in ids)
    return '<ul class="search-results__list">' + cards + '</ul>' + following


DETAIL = ('<article itemscope itemtype="https://schema.org/Book">'
          '<h1 itemprop="name">Fiche synthétique</h1>'
          '<span itemprop="author">Auteur de la fiche</span>'
          '<button class="button-borrow">Emprunter</button></article>')


class HTTPCatalogTests(unittest.TestCase):
    def make(self):
        session, calls, state = HTTPProviderTests().make()
        base = session.client.http._transport.handler
        state.update(pages={'1':search_html()}, detail=DETAIL, statuses={}, redirects={})
        requests = []
        def handler(request):
            if request.url.host=='pret.bnf.fr' and request.url.path in {'/results','/resources/book-a'}:
                calls.append((request.method,request.url.host,request.url.path))
                requests.append(request)
                if request.url.path=='/results':
                    page=request.url.params.get('page','1')
                    if page in state['redirects']:
                        return httpx.Response(302,headers={'location':state['redirects'].pop(page)})
                    return httpx.Response(state['statuses'].get(page,200),
                                          text=state['pages'].get(page,'<html>Inconnu</html>'),
                                          headers={'content-type':'text/html'})
                return httpx.Response(200,text=state['detail'],headers={'content-type':'text/html'})
            return base(request)
        session.client.http._transport.handler=handler
        self.addCleanup(session.close)
        return session,calls,state,requests

    def test_public_search_metadata_and_detail_without_login_or_right(self):
        session,calls,state,requests=self.make()
        rows=session.search('fiction française')
        row,=rows
        self.assertEqual(requests[0].url.params['q'],'fiction française')
        self.assertEqual((row.source_provider,row.source_credential,row.record_id),('bnf-pret','credential-a','book-a'))
        self.assertEqual(row.author,'Auteur fictif')
        self.assertEqual(row.publisher,'Éditeur fictif')
        self.assertEqual(row.metadata['languages'],['fr'])
        self.assertEqual(row.metadata['isbn'],'9780000000002')
        self.assertIsNone(row.owned)
        self.assertFalse(row.downloadable)
        self.assertIsNone(row.download_url)
        self.assertIsNone(row.format)
        self.assertTrue(session.last_search_warnings)
        detail=session.holdings(row)
        self.assertEqual(detail.title,'Fiche synthétique')
        self.assertEqual(detail.author,'Auteur de la fiche')
        self.assertEqual(detail.availability_status,'unknown')
        self.assertEqual(detail.holdings,[])
        self.assertFalse(session.connected)
        self.assertTrue(all(method=='GET' and host=='pret.bnf.fr' for method,host,path in calls))
        self.assertFalse(any('/loans/' in path or '/sso/' in path for _,_,path in calls))

    def test_only_observed_next_link_is_followed_and_duplicate_is_deduplicated(self):
        session,calls,state,requests=self.make()
        state['pages']['1']=search_html(('book-a','homonym-a'),
                '<nav class="pagination"><a rel="next" href="/results?q=fiction&amp;page=2">Suite</a></nav>')
        state['pages']['2']=search_html(('book-a','homonym-b'))
        rows=session.search('fiction')
        self.assertEqual({r.record_id for r in rows},{'book-a','homonym-a','homonym-b'})
        self.assertEqual([r.url.params.get('page','1') for r in requests],['1','2'])

    def test_later_403_preserves_observed_records_and_warns(self):
        session,calls,state,requests=self.make()
        state['pages']['1']=search_html(following='<a rel="next" href="/results?q=fiction&amp;page=2">Suite</a>')
        state['statuses']['2']=403
        rows=session.search('fiction')
        self.assertEqual(len(rows),1)
        self.assertIn('access_denied',session.last_search_warnings)
        self.assertEqual(len(requests),2)

    def test_first_page_refusals_have_core_codes_without_retry_or_fake_zero(self):
        from cifereca.search_failures import search_failure_code
        from cifereca.plugin_api import ProviderError
        for status,code in [(401,'SearchAuthenticationRequired'),(403,'SearchAccessDenied'),(429,'SearchTemporarilyUnavailable')]:
            session,calls,state,requests=self.make()
            state['statuses']['1']=status
            with self.assertRaises(ProviderError) as result:
                session.search('fiction')
            self.assertEqual(search_failure_code(result.exception),code)
            self.assertEqual(len(requests),1)

    def test_invalid_next_link_never_fetches_foreign_mutator_or_changed_query(self):
        for link in ['https://evil.test/results?q=fiction&amp;page=2',
                     '/loans/loan-a','/v1/resources','/results?q=other&amp;page=2',
                     '/results?q=fiction&amp;page=8', '/results?q=fiction&amp;page=2&amp;token=PRIVATE']:
            session,calls,state,requests=self.make()
            state['pages']['1']=search_html(following=f'<a rel="next" href="{link}">Suite</a>')
            self.assertEqual(len(session.search('fiction')),1)
            self.assertIn('next_page_unavailable',session.last_search_warnings)
            self.assertEqual(len(requests),1)

    def test_page_budget_stops_after_five_observed_pages(self):
        session,calls,state,requests=self.make()
        for n in range(1,6):
            state['pages'][str(n)]=search_html((f'book-{n}',),
                f'<a rel="next" href="/results?q=fiction&amp;page={n+1}">Suite</a>')
        self.assertEqual(len(session.search('fiction')),5)
        self.assertEqual(len(requests),5)
        self.assertIn('search_page_limit',session.last_search_warnings)

    def test_bad_html_and_login_form_are_not_empty_catalogues(self):
        from cifereca.plugin_api import ProviderError
        for html in ['<html>Inconnu</html>', LOGIN, '<ul class="search-results__list"><li>Tronqué']:
            session,calls,state,requests=self.make()
            state['pages']['1']=html
            with self.assertRaises(ProviderError):
                session.search('fiction')
            self.assertEqual(len(requests),1)

    def test_explicit_empty_is_still_non_exhaustive(self):
        session,calls,state,requests=self.make()
        state['pages']['1']=search_html(())
        self.assertEqual(session.search('fiction'),[])
        self.assertEqual(session.last_search_warnings,['inventory_not_exhaustive'])

    def test_unsupported_options_and_invalid_query_are_refused_without_io(self):
        from cifereca.plugin_api import ProviderCapabilityError, ProviderError
        session,calls,state,requests=self.make()
        for kwargs in [{'scope':'physical'},{'field':'isbn'},{'page':1},{'page':True},{'content_types':('audio',)}]:
            with self.assertRaises(ProviderCapabilityError):session.search('fiction',**kwargs)
        for query in ['', 'a', 'a'*129, 'test\nprivate',None]:
            with self.assertRaises(ProviderError):session.search(query)
        self.assertEqual(requests,[])

    def test_search_redirect_mismatch_and_detail_scope_are_refused(self):
        from dataclasses import replace
        from cifereca.plugin_api import ProviderError
        session,calls,state,requests=self.make()
        row,=session.search('fiction')
        for item in [replace(row,source_provider='paris'),replace(row,source_credential='other'),replace(row,record_id='../loans/a')]:
            with self.assertRaises((ProviderConfigurationError,ProviderError)):session.holdings(item)
        self.assertEqual(len(requests),1)
        state['redirects']['1']='https://pret.bnf.fr/results?q=other'
        with self.assertRaisesRegex(ProviderError,'search_redirect_mismatch'):session.search('fiction')

    def test_explicit_unavailability_is_separate_from_borrow_button(self):
        session,calls,state,requests=self.make()
        row,=session.search('fiction')
        state['detail']=DETAIL.replace('</article>','<p class="lb_unavailable">Indisponible</p></article>')
        self.assertEqual(session.holdings(row).availability_status,'unavailable')
        self.assertFalse(session.capabilities.borrow)
        self.assertFalse(session.capabilities.digital_download)

    def test_close_during_search_discards_inflight_results(self):
        session,calls,state,requests=self.make()
        request=session.client.request
        def closing(*args,**kwargs):
            result=request(*args,**kwargs)
            session.close()
            return result
        session.client.request=closing
        with self.assertRaisesRegex(ProviderConfigurationError,'session_closed'):session.search('fiction')


def test_catalogue_uses_core_search_and_detail_without_entitlement(tmp_path):
    sys.path.insert(0, str(ROOT.parents[2]))
    from tests.test_sync_state_contract import _service
    service = _service(tmp_path)
    case = HTTPCatalogTests()
    session, calls, state, requests = case.make()
    try:
        account, = session.connect()
        account.identity_id = 'default'
        service.accounts = [account]
        service.registry.register(PLUGIN)
        service._sessions[service._session_key('bnf-pret', 'credential-a')] = session
        rows = service.search('fiction', provider_id='bnf-pret', scope='digital', force=True)
        assert len(rows) == 1
        assert rows[0].metadata['isbn'] == '9780000000002'
        assert service.last_search_warnings
        detail = service.holdings(rows[0], force=True)
        assert detail.title == 'Fiche synthétique'
        assert detail.availability_status == 'unknown'
        batches = []
        summary = service.search_progressive('fiction', provider_id='bnf-pret',
                                             scope='digital', force=True, on_batch=batches.append)
        assert summary.result_count == 1
        assert summary.providers_failed == 0
        assert summary.warnings and batches
        assert service.storage.entitlements() == []
        assert service.storage.acquisitions() == []
        assert service.storage.placements() == []
        assert not any(method != 'GET' or path.startswith(('/loans/', '/v1/'))
                       for method, host, path in calls
                       if host == 'pret.bnf.fr' and not path.startswith('/sso/'))
    finally:
        service.close()


if __name__ == '__main__':
    unittest.main()
