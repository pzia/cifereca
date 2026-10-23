"""Opt-in real Chromium acceptance against synthetic, intercepted HTML only.

CIFERECA_BNF_CHROMIUM_TEST=1 python -m unittest discover \
    -s src/plugins/provider-bnf-pret/tests -p test_browser_chromium.py -v
No network request is allowed to reach an upstream site. SSO is not simulated
as an identity proof: this only exercises browser lifecycle/DOM/request guards.
"""
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    import cifereca_bnf_pret_browser as browser
finally:
    sys.path.pop(0)

HTML = {
    '/': '<html><body>Portail synthétique</body></html>',
    '/my_profile/activity': '<html><body><section class="active-loans">'
        '<div class="js-loan"><input type="hidden" name="SAMLResponse" value="SYNTHETIC_SECRET">'
        '<a href="/resources/synthetic-book">Titre fictif</a>'
        '<a href="/loans/synthetic-loan?signature=SYNTHETIC_SECRET">Télécharger</a>'
        '</div></section></body></html>',
    '/results': '<html><body><ul class="search-results__list">'
        '<li class="search-resource-result"><a href="/resources/synthetic-book">'
        'Titre fictif</a></li></ul></body></html>',
    '/resources/synthetic-book': '<html><body><article itemscope itemtype="https://schema.org/Book">'
        '<h1 itemprop="name">Titre fictif</h1><meta itemprop="isbn" content="9780000000002">'
        '<span itemprop="author" itemscope itemtype="https://schema.org/Person">'
        '<span itemprop="name">Auteur fictif</span></span>'
        '<button class="button-borrow">Emprunter</button>'
        '<p class="lb_unavailable">Indisponible</p></article></body></html>',
}


class FixtureRoute:
    """Keep the production request policy, substitute only the upstream bytes."""
    def __init__(self, route):
        self.route = route
        self.request = route.request

    def continue_(self):
        path = urlsplit(self.request.url).path
        self.route.fulfill(status=200 if path in HTML else 404,
                           content_type='text/html', body=HTML.get(path, 'Not found'))

    def abort(self, reason):
        self.route.abort(reason)


class FixtureContext:
    def __init__(self, context):
        self.context = context
        self.context.route('**/*', lambda route: FixtureRoute(route).continue_())

    def new_page(self):
        return self.context.new_page()

    def route(self, pattern, callback):
        self.context.route(pattern, lambda route: callback(FixtureRoute(route)))

    def route_web_socket(self, pattern, callback):
        self.context.route_web_socket(pattern, callback)

    def close(self):
        self.context.close()


class FixtureBrowser:
    def __init__(self, browser):
        self.browser = browser

    def new_context(self, **kwargs):
        return FixtureContext(self.browser.new_context(**kwargs))

    def close(self):
        self.browser.close()


class HeadlessFixtureFactory:
    def start(self):
        from playwright.sync_api import sync_playwright
        self.runtime = sync_playwright().start()
        return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **_:
            FixtureBrowser(self.runtime.chromium.launch(headless=True))), stop=self.runtime.stop)


@unittest.skipUnless(os.environ.get('CIFERECA_BNF_CHROMIUM_TEST') == '1',
                     'Real Chromium gate not enabled')
class ChromiumReaderTests(unittest.TestCase):
    def setUp(self):
        self.session = browser.ReaderBrowserSession('synthetic-identity', 'synthetic-account',
                                                     factory=HeadlessFixtureFactory)
        self.addCleanup(self.session.close)
        self.session.open_login(consent=True)
        self.snapshot = self.session.finish_login(account_confirmed=True)

    def test_real_dom_projection_strips_form_and_signatures(self):
        self.assertEqual(len(self.snapshot.loans), 1)
        payload = self.session._page.evaluate(browser.EXTRACT_DOM, dict(kind='loans',
            maxBytes=browser.MAX_HTML_BYTES, maxNodes=browser.MAX_NODES, maxDepth=browser.MAX_DEPTH))
        self.assertEqual(payload['code'], 'ok')
        self.assertNotIn('SYNTHETIC_SECRET', payload['html'])
        self.assertNotIn('SAMLResponse', payload['html'])
        detail = self.session.detail('synthetic-book', expected=self.session.scope)
        self.assertEqual(detail.records[0].isbn, '9780000000002')
        self.assertEqual(detail.records[0].authors, ('Auteur fictif',))
        self.assertEqual(detail.records[0].availability, 'unavailable')
        visible = self.session.current_detail(expected=self.session.scope)
        self.assertEqual(visible.records[0].title, 'Titre fictif')

    def test_real_explicit_readonly_search_uses_html_route(self):
        result = self.session.search('Fiction française', expected=self.session.scope)
        self.assertEqual(result.records[0].resource_id, 'synthetic-book')
        self.assertEqual(result.records[0].title, 'Titre fictif')
        self.assertIn('/results?q=Fiction%20fran%C3%A7aise', self.session._page.url)

    def test_real_dom_probe_emits_only_selector_counts(self):
        proof = self.session.probe_visible_dom(expected=self.session.scope)
        self.assertEqual((proof.kind, proof.loan_sections, proof.loan_cards,
                          proof.resource_links, proof.loan_links), ("activity", 1, 1, 1, 1))
        self.assertNotIn("SYNTHETIC_SECRET", repr(proof))
        self.session.search("fiction", expected=self.session.scope)
        report = self.session.probe_visible_dom(expected=self.session.scope)
        self.assertEqual((report.kind, report.search_sections, report.search_cards),
                         ("results", 1, 1))
        self.assertEqual(report.loan_cards, 0)

    def test_real_request_guard_blocks_post_and_loan_get(self):
        results = self.session._page.evaluate("""async () => {
          const blocked = async (url, options) => {
            try { await fetch(url, options); return false; } catch (_) { return true; }
          };
          return [await blocked('/resources/synthetic-book', {method:'POST'}),
                  await blocked('/loans/synthetic-loan', {}),
                  await blocked('/v1/resources.json', {})];
        }""")
        self.assertEqual(results, [True, True, True])


if __name__ == '__main__':
    unittest.main()
