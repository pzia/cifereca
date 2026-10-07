from __future__ import annotations
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("cifereca_gutenberg",ROOT/"cifereca_gutenberg.py")
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class NavigationCoverageTests(unittest.TestCase):
    def test_language_prefix(self):
        self.assertEqual(mod._search_facet("lang:fr","all"),("French","language","fr"))
    def test_bookshelf_prefix(self):
        self.assertEqual(mod._search_facet("bookshelf:Science Fiction","all"),("Science Fiction","bookshelf","Science Fiction"))

if __name__=="__main__":
    unittest.main()
