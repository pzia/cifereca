from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import datasetctl
import plugin_scenarios


class SpecializedLifecycleTests(unittest.TestCase):
    def profile(self, name="specialized-test", seed=20261008, enabled=True):
        return {
            "name": name,
            "library": {"epubs": 0},
            "cifereca": {"copy_installed_plugins": False},
            "services": {},
            "specialized": {"enabled": enabled, "seed": seed},
        }

    def test_profiles_enable_specialized_data_and_two_enable_ebooksgratuits(self):
        catalog = datasetctl.load_catalog()
        for name in ("ux-small", "ux-all", "ux-errors"):
            _, profile = datasetctl.load_profile(name)
            self.assertTrue(profile["specialized"]["enabled"])
            self.assertEqual(profile["specialized"]["seed"], plugin_scenarios.SEED)
            if name in ("ux-small", "ux-all"):
                self.assertIn("ebooksgratuits", datasetctl.enabled_service_configs(profile))
                self.assertIn("provider.ebooksgratuits", datasetctl.derived_plugins(profile, catalog))

    def test_generated_files_are_reproducible_repaired_and_removed_on_reset(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            datasetctl.RUNTIME_ENV: tmp, "CIFERECA_DATASET_PLUGIN_SOURCE": str(Path(tmp)/"empty")
        }):
            profile = self.profile()
            catalog = datasetctl.load_catalog()
            first = datasetctl.prepare(profile, catalog)
            root = first["specialized"]
            self.assertEqual(root, Path(tmp) / "specialized-test" / "specialized")
            self.assertTrue(root.is_dir())
            self.assertEqual(first["env"]["CIFERECA_UX_SPECIALIZED_DATA"], str(root))
            self.assertTrue(datasetctl.summary_payload(profile, catalog)["specialized"]["complete"])
            manifest = json.loads((root / "manifest.json").read_text())
            self.assertEqual(len(manifest["files"]), 7)

            second = datasetctl.prepare(profile, catalog)
            self.assertEqual(json.loads((second["specialized"] / "manifest.json").read_text()), manifest)
            (root / "youth.json").write_text("CORROMPU", encoding="utf-8")
            self.assertFalse(datasetctl.specialized_status(profile)["complete"])
            datasetctl.prepare(profile, catalog)
            self.assertTrue(datasetctl.specialized_status(profile)["complete"])
            self.assertEqual(json.loads((root / "manifest.json").read_text()), manifest)

            new_seed_profile = self.profile(seed=20261009)
            datasetctl.prepare(new_seed_profile, catalog)
            updated = json.loads((root / "manifest.json").read_text())
            self.assertEqual(updated["seed"], 20261009)
            self.assertNotEqual(manifest["files"]["optimizer-valid.epub"],
                                updated["files"]["optimizer-valid.epub"])

            with zipfile.ZipFile(root / "optimizer-valid.epub") as z:
                self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in z.infolist()))

            datasetctl.reset_runtime(profile)
            self.assertFalse(root.exists())
            self.assertFalse(datasetctl.specialized_status(profile)["complete"])

    def test_disabled_mode_does_not_create_or_export_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            datasetctl.RUNTIME_ENV: tmp, "CIFERECA_DATASET_PLUGIN_SOURCE": str(Path(tmp)/"empty")
        }):
            profile = self.profile(enabled=False)
            prepared = datasetctl.prepare(profile, datasetctl.load_catalog())
            self.assertIsNone(prepared["specialized"])
            self.assertNotIn("CIFERECA_UX_SPECIALIZED_DATA", prepared["env"])
            self.assertFalse((Path(tmp) / "specialized-test" / "specialized").exists())


if __name__ == "__main__":
    unittest.main()
