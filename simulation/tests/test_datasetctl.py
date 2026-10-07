from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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

    def test_ux_all_library_uses_identity_provider_layout(self):
        catalog = datasetctl.load_catalog()
        _, profile = datasetctl.load_profile("ux-all")
        old = os.environ.get(datasetctl.RUNTIME_ENV)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ[datasetctl.RUNTIME_ENV] = tmp
                prepared = datasetctl.prepare(profile, catalog, rebuild_library=True)
                library = Path(prepared["library"])
                epubs = list((library / "ux" / "local").glob("*.epub"))
                self.assertEqual(len(epubs), 1000)
                self.assertFalse(any(library.glob("*.epub")))
                summary = datasetctl.summary_payload(profile, catalog)
                self.assertEqual(summary["library"]["generated_epubs"], 1000)
                self.assertTrue(summary["library"]["complete"])
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

    def test_plugin_packages_are_copied_into_isolated_data_home(self):
        old_runtime = os.environ.get(datasetctl.RUNTIME_ENV)
        old_source = os.environ.get("CIFERECA_DATASET_PLUGIN_SOURCE")
        try:
            with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as src:
                os.environ[datasetctl.RUNTIME_ENV] = tmp
                os.environ["CIFERECA_DATASET_PLUGIN_SOURCE"] = src
                package = Path(src) / "org.example" / "1.0.0"
                package.mkdir(parents=True)
                (Path(src) / "org.example" / "current.json").write_text(
                    json.dumps({"active": "1.0.0", "previous": None}), encoding="utf-8"
                )
                (package / "cifereca-plugin.json").write_text(
                    json.dumps({
                        "manifest": 1,
                        "package": {"id": "org.example", "version": "1.0.0", "name": "Example", "publisher": "test", "plugin_api": ">=1,<2"},
                        "plugins": [{"id": "provider.example"}],
                    }),
                    encoding="utf-8",
                )
                profile = {"name": "unit-plugins", "cifereca": {}, "services": {}}
                target = datasetctl.sync_plugin_packages(profile)
                self.assertIsNotNone(target)
                self.assertIn("provider.example", datasetctl.installed_plugin_ids(target))
                self.assertNotEqual(Path(target).resolve(), Path(src).resolve())
        finally:
            if old_runtime is None:
                os.environ.pop(datasetctl.RUNTIME_ENV, None)
            else:
                os.environ[datasetctl.RUNTIME_ENV] = old_runtime
            if old_source is None:
                os.environ.pop("CIFERECA_DATASET_PLUGIN_SOURCE", None)
            else:
                os.environ["CIFERECA_DATASET_PLUGIN_SOURCE"] = old_source


if __name__ == "__main__":
    unittest.main()
