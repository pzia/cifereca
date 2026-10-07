from __future__ import annotations
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("cifereca_gallica",ROOT/"cifereca_gallica.py")
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class PdfSearchCoverageTests(unittest.TestCase):
    def test_ebook_cql_covers_epub_and_pdf(self):
        self.assertIn('"epub"',mod.EBOOK_FORMAT_CQL)
        self.assertIn('"pdf"',mod.EBOOK_FORMAT_CQL)
        self.assertIn(" or ",mod.EBOOK_FORMAT_CQL)

if __name__=="__main__":
    unittest.main()
