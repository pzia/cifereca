import unittest
from cifereca_ebooksgratuits import EbooksSession, Parser

HTML='<ul><li><a href="details.php?book=1"><span class="listeb">Titre &amp; test</span></a><span class="auteur">Auteur A</span><p class="resume">Résumé utile</p><img src="covers/1.jpg"><a href="newsendbook.php?id=1&amp;format=epub">ePub</a></li></ul>'
class Improvements(unittest.TestCase):
    def test_offset_is_server_side(self): self.assertEqual(EbooksSession._search_payload('abc',2,50)['offset'],'100')
    def test_structured_card_parser(self):
        row=Parser().rows(HTML)[0]; self.assertEqual(row['title'],'Titre & test'); self.assertEqual(row['author'],'Auteur A'); self.assertTrue(row['epub'].endswith('format=epub')); self.assertTrue(row['image'].endswith('/covers/1.jpg'))
if __name__ == '__main__': unittest.main()
