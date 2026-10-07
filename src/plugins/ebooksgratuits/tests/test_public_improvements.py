import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from cifereca_ebooksgratuits import EbooksSession, Parser

HTML='<ul><li><a href="details.php?book=1"><span class="listeb">Titre &amp; test</span></a><span class="auteur">Auteur A</span><p class="resume">Résumé utile</p><img src="covers/1.jpg"><a href="newsendbook.php?id=1&amp;format=epub">ePub</a></li></ul>'


class FakeTransport:
    def __init__(self):
        self.calls=[]

    def fetch(self,source,destination):
        destination=Path(destination)
        self.calls.append((dict(source),destination))
        destination.write_bytes(b'delegated')
        return destination


class Improvements(unittest.TestCase):
    def test_offset_is_server_side(self):
        self.assertEqual(EbooksSession._search_payload('abc',2,50)['offset'],'100')

    def test_structured_card_parser(self):
        row=Parser().rows(HTML)[0]
        self.assertEqual(row['title'],'Titre & test')
        self.assertEqual(row['author'],'Auteur A')
        self.assertTrue(row['epub'].endswith('format=epub'))
        self.assertTrue(row['image'].endswith('/covers/1.jpg'))

    def test_final_epub_download_is_delegated_to_http_transport(self):
        transport=FakeTransport()
        session=EbooksSession(
            credential_id='public',
            label='',
            credentials={},
            options={'max_download_bytes':4321},
            config={},
            diagnostics=None,
            transport=transport,
        )
        item=SimpleNamespace(
            source_provider='ebooksgratuits',
            download_url='https://www.ebooksgratuits.com/downloads/book.epub',
            friendly_url='https://www.ebooksgratuits.com/details.php?book=1',
            title='Titre test',
            record_id='book-1',
        )
        try:
            with TemporaryDirectory() as tmp:
                path,source_url=session.download_direct(item,Path(tmp))
                self.assertTrue(path.is_file())
                self.assertEqual(source_url,item.friendly_url)
                self.assertEqual(len(transport.calls),1)
                source,destination=transport.calls[0]
                self.assertEqual(source['url'],item.download_url)
                self.assertEqual(source['max_bytes'],4321)
                self.assertEqual(source['expected_format'],'epub')
                self.assertTrue(source['overwrite'])
                self.assertEqual(destination,path)
        finally:
            session.close()


if __name__ == '__main__':
    unittest.main()
