import unittest
from cifereca.paris.normalize import microsoft_date_to_iso, normalize_loan, normalize_history

class NormalizeTests(unittest.TestCase):
    def test_microsoft_date(self):
        value = microsoft_date_to_iso('/Date(1791955718733+0200)/')
        self.assertTrue(value and value.startswith('2026-'))

    def test_digital_loan(self):
        loan = normalize_loan({
            'Id':'x','RecordId':'9780000000000','Title':'Livre','WhenBack':'/Date(1791955718733+0200)/',
            'CanRenew':False,'AdditionalProperties':{'AccessMedium':'DOWNLOAD','CanReturn':True,'HasBeenRenewed':False,'EbookUrl':'https://example.invalid/x'}
        }, 'DILICOM')
        self.assertEqual(loan.medium, 'digital')
        self.assertTrue(loan.can_return)
        self.assertEqual(loan.access_medium, 'DOWNLOAD')

    def test_history(self):
        item = normalize_history({
            'Id':'h','Title':'Titre','LoanDate':'/Date(1750000000000+0200)/',
            'ThumbnailUrl': None, 'DefaultThumbnailUrl': 'https://example/default.png',
            'AdditionalProperties':{'Auteur':'A','Annee':'2020'}
        }, 'SYRACUSE')
        self.assertEqual(item.author, 'A')
        self.assertEqual(item.year, '2020')
        self.assertIsNone(item.thumbnail_url)
        self.assertEqual(item.default_thumbnail_url, 'https://example/default.png')

if __name__ == '__main__': unittest.main()
