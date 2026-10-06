import tempfile
import unittest
from pathlib import Path

from cifereca.diagnostics import Diagnostics
from cifereca.models import SearchResult
from cifereca.services import LibraryService
from cifereca.storage import Storage


class DummyConfig:
    def get(self, key, default=None):
        values = {
            'base_url': 'https://example.test',
            'request_timeout_seconds': 2,
            'cache.history_seconds': 1800,
        }
        return values.get(key, default)

    def set(self, *_args):
        pass

    def save(self):
        pass


def result(record_id: str, title: str, author: str | None, year: str | None, thumbnail: str | None) -> SearchResult:
    return SearchResult(
        id=f'SYRACUSE:{record_id}',
        record_id=record_id,
        docbase='SYRACUSE',
        medium='physical',
        title=title,
        author=author,
        type='Livre',
        format=None,
        publisher=None,
        published_at=year,
        description=None,
        thumbnail_url=thumbnail,
        friendly_url=None,
        scenario='CATALOGUE',
    )


class _Session:
    def __init__(self, search, holdings=None):
        self._search = search
        self._holdings = holdings
    def search(self, query, scope="all", page=0, field="all"):
        return self._search(query, scope=scope, page=page, field=field)
    def holdings(self, item, search_terms=""):
        if self._holdings is None:
            raise RuntimeError("holdings unavailable")
        return self._holdings(item, search_terms=search_terms)


class HistoryCoverTests(unittest.TestCase):
    def test_resolve_history_thumbnail_uses_search_and_cache(self):
        with tempfile.TemporaryDirectory() as td:
            service = LibraryService(DummyConfig(), Storage(Path(td) / 'x.sqlite3'), Diagnostics(level='detailed'))
            calls = []

            def search_catalog(query, scope='all', page=0, field='all'):
                calls.append((query, scope, page, field))
                return [
                    result('999', 'Stonehenge', 'Patrick Sobral', '2020', 'https://covers.example/real.jpg'),
                    result('998', 'Stonehenge : guide illustré', 'Autre Auteur', '2018', 'https://covers.example/other.jpg'),
                ]

            service._session_for_account = lambda *_args, **_kwargs: _Session(search_catalog)
            self.assertEqual(
                service.resolve_history_thumbnail('SYRACUSE', '123', 'Stonehenge', 'Sobral, Patrick', '2020', 'https://example/default.png'),
                ('https://covers.example/real.jpg', 'matched'),
            )
            self.assertEqual(
                service.resolve_history_thumbnail('SYRACUSE', '123', 'Stonehenge', 'Sobral, Patrick', '2020', 'https://example/default.png'),
                ('https://covers.example/real.jpg', 'matched'),
            )
            self.assertEqual(calls, [('Stonehenge', 'all', 0, 'all')])
            service.close()

    def test_resolve_history_thumbnail_ambiguous_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as td:
            service = LibraryService(DummyConfig(), Storage(Path(td) / 'x.sqlite3'), Diagnostics())

            def search_catalog(*_args, **_kwargs):
                return [
                    result('1', 'Le voyage', None, None, 'https://covers.example/a.jpg'),
                    result('2', 'Le voyage', None, None, 'https://covers.example/b.jpg'),
                ]

            service._session_for_account = lambda *_args, **_kwargs: _Session(search_catalog)
            url, status = service.resolve_history_thumbnail(
                'SYRACUSE', None, 'Le voyage', None, None, 'https://example/default.png'
            )
            self.assertEqual(url, 'https://example/default.png')
            self.assertEqual(status, 'ambiguous')
            service.close()

    def test_resolve_history_thumbnail_not_found_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as td:
            service = LibraryService(DummyConfig(), Storage(Path(td) / 'x.sqlite3'), Diagnostics())
            service._session_for_account = lambda *_args, **_kwargs: _Session(lambda *_a, **_k: [])
            self.assertEqual(
                service.resolve_history_thumbnail('SYRACUSE', '123', 'Titre introuvable', None, None, 'https://example/default.png'),
                ('https://example/default.png', 'not_found'),
            )
            service.close()

    def test_exact_record_id_is_strong_match(self):
        with tempfile.TemporaryDirectory() as td:
            service = LibraryService(DummyConfig(), Storage(Path(td) / 'x.sqlite3'), Diagnostics())
            service._session_for_account = lambda *_args, **_kwargs: _Session(lambda *_a, **_k: [
                result('123', 'Titre proche mais différent', None, None, 'https://covers.example/record.jpg')
            ])
            self.assertEqual(
                service.resolve_history_thumbnail('SYRACUSE', '123', 'Titre', None, None, None),
                ('https://covers.example/record.jpg', 'matched'),
            )
            service.close()

    def test_confident_search_result_without_thumbnail_uses_detail_cover(self):
        from cifereca.models import ItemDetail
        with tempfile.TemporaryDirectory() as td:
            service = LibraryService(DummyConfig(), Storage(Path(td) / 'x.sqlite3'), Diagnostics(level='detailed'))
            search = lambda *_args, **_kwargs: [
                result('123', 'Stonehenge', 'Patrick Sobral', '2020', None)
            ]
            holdings = lambda *_args, **_kwargs: ItemDetail(
                record_id='123', docbase='SYRACUSE', title='Stonehenge',
                author='Patrick Sobral', type='BD', year='2020',
                thumbnail_url='https://covers.example/detail.jpg',
                availability_status='available', availability_label='Disponible', holdings=[]
            )
            service._session_for_account = lambda *_args, **_kwargs: _Session(search, holdings)
            self.assertEqual(
                service.resolve_history_thumbnail('SYRACUSE', '123', 'Stonehenge', 'Sobral, Patrick', '2020', None),
                ('https://covers.example/detail.jpg', 'matched'),
            )
            service.close()

    def test_search_retries_with_simplified_title(self):
        with tempfile.TemporaryDirectory() as td:
            service = LibraryService(DummyConfig(), Storage(Path(td) / 'x.sqlite3'), Diagnostics(level='detailed'))
            calls = []
            def search_catalog(query, scope='all', page=0, field='all'):
                calls.append(query)
                if len(calls) == 1:
                    return []
                return [result('1', 'Le voyage', None, None, 'https://covers.example/voyage.jpg')]
            service._session_for_account = lambda *_args, **_kwargs: _Session(search_catalog)
            url, status = service.resolve_history_thumbnail(
                'SYRACUSE', None, 'Le voyage : édition illustrée', None, None, None
            )
            self.assertEqual(status, 'matched')
            self.assertEqual(url, 'https://covers.example/voyage.jpg')
            self.assertGreaterEqual(len(calls), 2)
            service.close()


if __name__ == '__main__':
    unittest.main()
