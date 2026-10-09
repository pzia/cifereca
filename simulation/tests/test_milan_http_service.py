"""Contrat du serveur Milan ; les assertions métier restent dans le plugin."""
from http.server import ThreadingHTTPServer
from pathlib import Path
import json
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from youth_publishers.pseudo_youth_publishers_service import FakePublishers, Handler


class MilanHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = type('MilanHandler', (Handler,), {'app': FakePublishers()})
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.root = f'http://127.0.0.1:{cls.server.server_port}'
        cls.opener = build_opener(ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def get(self, path):
        try:
            response = self.opener.open(self.root + path, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def test_health_and_pagination_contract(self):
        self.assertIn('milan', json.loads(self.get('/health')[2])['publishers'])
        status, headers, body = self.get('/publishers/milan/livres/')
        self.assertEqual(status, 200)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertEqual(int(headers['Content-Length']), len(body))
        self.assertIn(b'/livres/page/2/', body)
        self.assertIn(b'outside.example', body)
        self.assertIn(b'1009-neuvieme', body)
        second = self.get('/publishers/milan/livres/page/2/')[2]
        self.assertIn(b'1009-neuvieme', second)
        self.assertNotIn(b'/livres/page/3/', second)
        self.assertNotIn(b'<a ', self.get('/publishers/milan/livres/page/3/')[2])

    def test_detail_evidence_and_negative_fixtures(self):
        first = self.get('/publishers/milan/livres/1001-lune/')[2].decode()
        for evidence in ('À partir de 3 ans', '9782408020804', 'Alice Exemple',
                         'Collection', 'Thèmes'):
            self.assertIn(evidence, first)
        cases = [('1003-sans-age', 'Âge', False),
                 ('1004-adulte', 'À partir de 18 ans', True),
                 ('1005-isbn-absent', 'Code ISBN', False),
                 ('1006-isbn-invalide', '9780000000000', True)]
        for slug, label, present in cases:
            with self.subTest(slug=slug):
                body = self.get(f'/publishers/milan/livres/{slug}/')[2].decode()
                self.assertEqual(label in body, present)

    def test_errors_are_deterministic_on_listing_and_product(self):
        for path in ('/livres/', '/livres/1001-lune/'):
            for fault, expected in (('404', 404), ('503', 503), ('wrong-type', 200),
                                    ('oversized', 200), ('malformed', 200)):
                with self.subTest(path=path, fault=fault):
                    result = self.get('/publishers/milan' + path + '?fault=' + fault)
                    self.assertEqual(result[0], expected)
                    if fault == 'oversized':
                        self.assertEqual(len(result[2]), 2_000_001)
                    elif fault == 'wrong-type':
                        self.assertEqual(result[1]['Content-Type'], 'application/json')
                    elif fault == 'malformed':
                        self.assertEqual(result[2], b'<html><a href="')

    def test_unknown_routes_and_invalid_pages(self):
        for path, status in (('/livres/999-absent/', 404),
                             ('/livres/page/nope/', 404),
                             ('/livres/page/0/', 400),
                             ('/livres/page/101/', 400)):
            with self.subTest(path=path):
                self.assertEqual(self.get('/publishers/milan' + path)[0], status)


if __name__ == '__main__':
    unittest.main()
