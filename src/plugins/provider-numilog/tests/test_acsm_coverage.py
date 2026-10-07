from __future__ import annotations
from pathlib import Path
import tempfile
import unittest

from cifereca.plugin_api import inspect_artifact


class AcsmCoverageTests(unittest.TestCase):
    def test_detects_adobe_fulfillment_token(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"book.acsm"
            path.write_text('<?xml version="1.0"?><fulfillmentToken xmlns="http://ns.adobe.com/adept"><resourceItemInfo/></fulfillmentToken>',encoding="utf-8")
            result=inspect_artifact(path,"acsm")
            self.assertTrue(result.valid)
            self.assertEqual(result.media_format,"acsm")

    def test_rejects_generic_xml(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"book.xml"
            path.write_text("<book/>",encoding="utf-8")
            self.assertFalse(inspect_artifact(path,"acsm").valid)


if __name__=="__main__":
    unittest.main()
