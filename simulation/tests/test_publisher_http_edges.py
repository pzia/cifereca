"""Self-tests des fixtures HTTP : filtres, nourrissons et erreurs déterministes."""
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from youth_publishers.pseudo_youth_publishers_service import Handler, FakePublishers


class PublisherHTTPEdges(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = type('PublisherHandler', (Handler,), {'app': FakePublishers()})
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.root = f'http://127.0.0.1:{cls.server.server_port}/publishers/'
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
            return response.status, response.read().decode('utf-8')

    def test_infant_labels_and_terminal_pages(self):
        ecole = 'ecole/recherche-avancee'
        self.assertIn('0 à 3 ans', self.get(ecole + '?page=2')[1])
        status, body = self.get(ecole + '?page=3')
        self.assertEqual(status, 200)
        self.assertNotIn('<a ', body)

    def test_search_filters_before_pagination_and_has_no_shared_state(self):
        path = 'ecole/recherche-avancee?search_api_views_fulltext='
        for query, expected, absent in [('LOUP', 'Petit Loup', 'Roman ado'),
                                        ('petit', 'Petit Loup', 'Roman ado'),
                                        ('roman', 'Roman ado', 'Petit Loup')]:
            with self.subTest(query=query):
                status, body = self.get(path + query)
                self.assertEqual(status, 200)
                self.assertIn(expected, body)
                self.assertNotIn(absent, body)
                self.assertNotIn('Afficher plus', body)
        # Deux filtres successifs ne doivent pas altérer le catalogue initial.
        self.assertIn('Petit Loup', self.get(path + 'petit')[1])
        self.assertIn('Tout petit', self.get(path + 'petit')[1])
        self.assertNotIn('<a ', self.get(path + 'no-such-title')[1])
        self.assertNotIn('<a ', self.get(path + 'petit&page=2')[1])
        self.assertNotIn('<a ', self.get('ecole/recherche-avancee?types=article')[1])

    def test_invalid_pages_are_explicit_errors(self):
        for path, param in [('ecole/recherche-avancee', 'page')]:
            for value in ('0', '-1', '101', 'bad'):
                with self.subTest(path=path, value=value):
                    self.assertEqual(self.get(f'{path}?{param}={value}')[0], 400)
            self.assertEqual(self.get(f'{path}?{param}=100')[0], 200)

    def test_malformed_payload_is_repeatable(self):
        for path in ('ecole/recherche-avancee',):
            first = self.get(path + '?fault=malformed')
            self.assertEqual(first, (200, '<html><a href="'))
            self.assertEqual(first, self.get(path + '?fault=malformed'))
            self.assertEqual(self.get(path + '?fault=503')[0], 503)


if __name__ == '__main__':
    unittest.main()
