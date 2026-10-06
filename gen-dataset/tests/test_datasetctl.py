from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

import datasetctl


class DatasetCtlTests(unittest.TestCase):
    def test_all_versioned_profiles_reference_known_services_and_unique_ports(self):
        catalog = datasetctl.load_catalog()
        paths = sorted(datasetctl.PROFILES_DIR.glob("*.toml"))
        self.assertTrue(paths)
        for path in paths:
            loaded_path, profile = datasetctl.load_profile(str(path))
            self.assertEqual(loaded_path, path.resolve())
            ports = []
            for name, config in datasetctl.enabled_service_configs(profile).items():
                self.assertIn(name, catalog, path.name)
                ctx = datasetctl.service_context(profile, catalog, name, config)
                self.assertTrue(ctx["script"].is_file(), path.name)
                ports.append(ctx["port"])
            self.assertEqual(len(ports), len(set(ports)), path.name)

    def test_prepare_writes_isolated_cifereca_configuration(self):
        catalog = datasetctl.load_catalog()
        profile = {
            "name": "unit",
            "cifereca": {"width": 1300, "height": 850, "no_plugins": True},
            "library": {"epubs": 0, "seed": 1},
            "services": {
                "opds": {"enabled": True, "books_per_source": 10, "seed": 1},
                "bnf": {"enabled": True, "books": 10, "seed": 1},
                "kobo": {"enabled": True, "catalog_books": 10, "vault_books": 2, "seed": 1},
                "paris": {"enabled": True, "books": 10, "active_loans": 1, "history_items": 1, "bookings": 1, "digital_ratio": 0.7, "seed": 1},
                "numilog": {"enabled": True, "books": 10, "page_size": 5, "seed": 1},
            },
        }
        old = os.environ.get(datasetctl.RUNTIME_ENV)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ[datasetctl.RUNTIME_ENV] = tmp
                prepared = datasetctl.prepare(profile, catalog)
                config = json.loads(Path(prepared["config"]).read_text(encoding="utf-8"))
                plugins = json.loads(Path(prepared["plugins_config"]).read_text(encoding="utf-8"))
                self.assertEqual(config["library"]["directory"], str(Path(tmp) / "unit" / "library"))
                providers = {row["provider"]: row for row in config["providers"]["accounts"]}
                self.assertEqual(providers["kobo"]["credentials"]["api_token"], "kvt_test")
                self.assertEqual(providers["paris"]["credentials"]["username"], "paris-test")
                self.assertEqual(providers["numilog"]["credentials"]["email"], "numilog-test@example.test")
                values = plugins["values"]
                self.assertIn("provider.gallica", values)
                self.assertIn("provider.gutenberg", values)
                self.assertIn("provider.bnf-catalogue", values)
                self.assertIn("provider.numilog", values)
        finally:
            if old is None:
                os.environ.pop(datasetctl.RUNTIME_ENV, None)
            else:
                os.environ[datasetctl.RUNTIME_ENV] = old

    def test_derived_plugins_are_deduplicated(self):
        catalog = datasetctl.load_catalog()
        _, profile = datasetctl.load_profile("ux-all")
        plugins = datasetctl.derived_plugins(profile, catalog)
        self.assertEqual(len(plugins), len(set(plugins)))
        self.assertIn("provider.kobo", plugins)
        self.assertIn("provider.bnf-catalogue", plugins)
        self.assertIn("ui.page.home", plugins)


if __name__ == "__main__":
    unittest.main()
