from __future__ import annotations
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("cifereca_library_audits",ROOT/"cifereca_library_audits.py")
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class IntegrityCoverageTests(unittest.TestCase):
    def test_missing_file(self):
        self.assertEqual(mod._file_integrity_issues("/definitely/missing/cifereca.epub"),("missing",))

    def test_pdf_header_and_hash(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"book.pdf"; path.write_bytes(b"not a pdf")
            issues=mod._file_integrity_issues(path,"pdf",expected_size=9,expected_sha256="0"*64)
            self.assertIn("sha256_mismatch",issues)
            self.assertIn("pdf_header",issues)

if __name__=="__main__":
    unittest.main()
