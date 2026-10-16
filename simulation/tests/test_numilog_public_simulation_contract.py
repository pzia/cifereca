"""Loopback public Numilog search simulation; no provider or external HTTP."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

SIM = Path(__file__).resolve().parents[1]
ROOT = SIM.parent
for folder in (SIM, ROOT / 'src', ROOT / 'src/plugins/provider-numilog'):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from numilog.pseudo_numilog_service import DatasetCache, Handler, NumilogFakeApp
from cifereca_numilog_public import PublicSearchParseError, parse_public_search

PUBLIC_URL = 'https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx'


class NumilogPublicSimulation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.cache = DatasetCache(Path(cls.tmp.name), 7, 20261016, rebuild=True)
        cls.app = NumilogFakeApp(cls.cache, 'test@example.test', 'test', 3)
        local_handler = type('LocalNumilogPublicHandler', (Handler,), {'app': cls.app})
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), local_handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.tmp.cleanup()

    def _get(self, query='', page=0, fault='', cookie=None):
        args = f'mode=brSimple&query={quote(query)}'
        if page:
            args += f'&GRID_PAGE={page}'
        if fault:
            args += f'&ux_fault={fault}'
        headers = {'Cookie': cookie} if cookie is not None else {}
        try:
            with urlopen(Request(self.base + '/Pages/Recherche/ResultatRecherche.aspx?' + args, headers=headers), timeout=3) as response:
                return response.status, response.headers, response.read(), args
        except HTTPError as exc:
            return exc.code, exc.headers, exc.read(), args

    def test_public_search_is_anonymous_and_never_grants_ownership(self):
        status, headers, html, query = self._get()
        self.assertEqual(status, 200)
        self.assertIsNone(headers.get('Set-Cookie'))
        page = parse_public_search(html.decode('utf-8'), PUBLIC_URL + '?' + query)
        self.assertEqual((page.total_results, len(page.items), page.page_index), (7, 3, 0))
        self.assertTrue(page.next_url and page.next_url.endswith('GRID_PAGE=1'))
        for item in page.items:
            self.assertIs(item.owned, False)
            self.assertIs(item.downloadable, False)
            self.assertIsNone(item.download_url)
            self.assertEqual((item.price, item.currency), ('9.99', 'EUR'))
            self.assertEqual(item.metadata['price']['applies_to'], 'displayed_card_offer')
            self.assertTrue(item.record_id.startswith('numilog:public:'))
        # A forged cookie cannot change public data or reveal bought content.
        authenticated = self._get(cookie='NUMILOG_FAKE=1')[2]
        self.assertEqual(html, authenticated)
        self.assertNotIn(b'fulfillment/URLLink.acsm', html)
        self.assertNotIn(b'/Pages/Compte/readBook.ashx', html)

    def test_pagination_has_distinct_products_and_no_phantom_next_page(self):
        products = []
        for index, expected_count in ((0, 3), (1, 3), (2, 1)):
            status, _headers, html, args = self._get(page=index)
            self.assertEqual(status, 200)
            page = parse_public_search(html.decode('utf-8'), PUBLIC_URL + '?' + args)
            self.assertEqual(len(page.items), expected_count)
            self.assertEqual(page.total_results, 7)
            self.assertEqual(page.page_index, index)
            self.assertEqual(page.next_url is not None, index != 2)
            products.extend(row.record_id for row in page.items)
        self.assertEqual(len(products), len(set(products)))

    def test_zero_results_are_distinct_from_html_layout_failures(self):
        status, _headers, body, args = self._get('NO_MATCH_SYNTHETIC_928747')
        self.assertEqual(status, 200)
        page = parse_public_search(body.decode('utf-8'), PUBLIC_URL + '?' + args)
        self.assertEqual(page.total_results, 0)
        self.assertFalse(page.items)
        for fault in ('login_html', 'bad_grid'):
            status, _headers, body, _args = self._get(fault=fault)
            self.assertEqual(status, 200)
            # Parser receives an official URL label, not loopback: no extra HTTP.
            with self.assertRaises(PublicSearchParseError):
                parse_public_search(body.decode('utf-8'), PUBLIC_URL + '?mode=brSimple&query=')

    def test_explicit_faults_are_bounded_and_private_route_unaffected(self):
        for fault, expected in (('429', 429), ('503', 503)):
            status, headers, body, _ = self._get(fault=fault)
            self.assertEqual(status, expected)
            self.assertFalse(body.startswith(b'<table'))
            if expected == 429:
                self.assertEqual(headers.get('Retry-After'), '60')
        status, _headers, body, _ = self._get()
        self.assertEqual(status, 200)
        self.assertIn(b'LivreDuThemeDetail', body)
        with self.assertRaises(HTTPError) as ctx:
            urlopen(self.base + '/Pages/Recherche/ResultatRecherche.aspx?mode=wrong&query=book', timeout=3)
        self.assertEqual(ctx.exception.code, 400)
        with urlopen(self.base + '/health', timeout=3) as response:
            self.assertEqual(response.status, 200)


if __name__ == '__main__':
    unittest.main()
