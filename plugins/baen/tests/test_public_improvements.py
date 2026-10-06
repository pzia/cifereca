import unittest
from cifereca_baen import BaenSession

class BaenImprovements(unittest.TestCase):
    def test_server_pagination_params(self):
        self.assertEqual(BaenSession._search_params(' free  stories ',2,50), {'page':3,'pageSize':50,'hideBundles':0,'hideEarcs':0,'q':'free stories'})

if __name__ == '__main__': unittest.main()
