from __future__ import annotations
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("cifereca_numilog",ROOT/"cifereca_numilog.py")
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class AcsmCoverageTests(unittest.TestCase):
    def test_detects_adobe_fulfillment_token(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"book.acsm"
            path.write_text('<?xml version="1.0"?><fulfillmentToken xmlns="http://ns.adobe.com/adept"><resourceItemInfo/></fulfillmentToken>',encoding="utf-8")
            self.assertTrue(mod.looks_like_acsm(path))
    def test_rejects_generic_xml(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"book.xml"; path.write_text("<book/>",encoding="utf-8")
            self.assertFalse(mod.looks_like_acsm(path))

if __name__=="__main__":
    unittest.main()
