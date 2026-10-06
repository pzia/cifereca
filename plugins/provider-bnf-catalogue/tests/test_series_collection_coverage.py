from __future__ import annotations
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("cifereca_bnf_catalogue",ROOT/"cifereca_bnf_catalogue.py")
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class StructuredMetadataTests(unittest.TestCase):
    def test_series_and_collections_ignore_urls(self):
        series,collections=mod._structured_group_values({
            "relation":["Les enquêtes de Maigret","https://gallica.bnf.fr/ark:/12148/x"],
            "source":["Collection Folio","urn:isbn:123"],
        })
        self.assertEqual(series,["Les enquêtes de Maigret"])
        self.assertEqual(collections,["Collection Folio"])

if __name__=="__main__":
    unittest.main()
