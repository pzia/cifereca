import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from cifereca_baen import BaenSession


class FakeResponse:
    text = '''<html><head><title>Example Book by Example Author - WebScription Ebook</title></head>
    <body><p>Published: 2/1/2000</p><p>ISBN: 0671578499</p><p>ISBN: 9781668073407</p>
    <a href="/downloads/book.epub">Download EPUB</a></body></html>'''

    def raise_for_status(self):
        return None


class FakeClient:
    def get(self, *_args, **_kwargs):
        return FakeResponse()

    def close(self):
        return None


class FakeTransport:
    def __init__(self):
        self.calls = []

    def fetch(self, source, destination):
        destination = Path(destination)
        self.calls.append((dict(source), destination))
        destination.write_bytes(b"delegated")
        return destination


class BaenImprovements(unittest.TestCase):
    def test_server_pagination_params(self):
        self.assertEqual(BaenSession._search_params(' free  stories ',2,50), {'p':3,'product_list_limit':36})

    def test_public_detail_metadata_is_attested_and_conservative(self):
        metadata = BaenSession._detail_metadata(FakeResponse.text)
        self.assertEqual(metadata["author"], "Example Author")
        self.assertEqual(metadata["published_at"], "2/1/2000")
        self.assertEqual(metadata["isbns"], ["0671578499", "9781668073407"])

    def test_holdings_enriches_search_result_without_private_account_data(self):
        session = BaenSession(
            credential_id="public",
            label="",
            credentials={},
            options={},
            config={},
            diagnostics=None,
            transport=None,
        )
        session.client.close()
        session.client = FakeClient()
        item = SimpleNamespace(
            source_provider="baen",
            friendly_url="https://www.baen.com/example.html",
            title="Example Book",
            author=None,
            type="Livre numérique",
            thumbnail_url=None,
            published_at=None,
            metadata={},
            record_id="book-1",
        )
        try:
            detail=session.holdings(item)
            self.assertEqual(item.author,"Example Author")
            self.assertEqual(item.published_at,"2/1/2000")
            self.assertEqual(item.metadata["isbns"],["0671578499","9781668073407"])
            self.assertEqual(item.metadata["metadata_evidence"],"baen.public_product_page")
            self.assertEqual(detail.author,"Example Author")
            self.assertEqual(detail.year,"2/1/2000")
        finally:
            session.close()

    def test_final_epub_download_is_delegated_to_http_transport(self):
        transport = FakeTransport()
        session = BaenSession(
            credential_id="public",
            label="",
            credentials={},
            options={"max_download_bytes": 1234},
            config={},
            diagnostics=None,
            transport=transport,
        )
        session.client.close()
        session.client = FakeClient()
        item = SimpleNamespace(
            source_provider="baen",
            friendly_url="https://www.baen.com/allbooks/example",
            title="Example Book",
            record_id="book-1",
        )
        try:
            with TemporaryDirectory() as tmp:
                path, source_url = session.download_direct(item, Path(tmp))
                self.assertTrue(path.is_file())
                self.assertEqual(source_url, item.friendly_url)
                self.assertEqual(len(transport.calls), 1)
                source, destination = transport.calls[0]
                self.assertEqual(source["url"], "https://www.baen.com/downloads/book.epub")
                self.assertEqual(source["max_bytes"], 1234)
                self.assertEqual(source["expected_format"], "epub")
                self.assertTrue(source["overwrite"])
                self.assertEqual(destination, path)
        finally:
            session.close()


if __name__ == '__main__':
    unittest.main()
