from __future__ import annotations

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class KoboCatalogContractTests(unittest.TestCase):
    def test_catalog_is_technical_service_and_provider_requires_public_contract(self):
        data = json.loads((ROOT / "cifereca-plugin.json").read_text(encoding="utf-8"))
        plugins = {row["id"]: row for row in data["plugins"]}
        self.assertEqual(plugins["provider.kobo.catalog"]["type"], "catalog.service")
        self.assertEqual(plugins["provider.kobo"]["requires"], [])
        self.assertEqual(
            plugins["provider.kobo"]["requires_contracts"],
            [{
                "category": "catalog.service",
                "contract": "cifereca.catalog.service",
                "version": ">=1,<2",
                "mode": "required",
                "capabilities": [],
            }],
        )
        source = (ROOT / "cifereca_kobo.py").read_text(encoding="utf-8")
        self.assertIn('context.select_service(', source)
        self.assertIn('"catalog.service"', source)
        self.assertNotIn("context.service('provider.kobo.catalog')", source)


if __name__ == "__main__":
    unittest.main()
