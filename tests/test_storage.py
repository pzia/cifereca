import tempfile, unittest
from pathlib import Path
from cifereca.storage import Storage

class StorageTests(unittest.TestCase):
    def test_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            s=Storage(Path(td)/'x.sqlite3')
            s.cache_put('n','k',{'a':1},60)
            self.assertEqual(s.cache_get('n','k'),{'a':1})
            s.cache_delete_namespace('n')
            self.assertIsNone(s.cache_get('n','k'))

if __name__ == '__main__': unittest.main()
