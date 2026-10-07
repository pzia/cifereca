from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SIMULATION = Path(__file__).resolve().parents[1]
if str(SIMULATION) not in sys.path:
    sys.path.insert(0, str(SIMULATION))

import datasetctl
import e2e_fixtures


class E2EFixtureTests(unittest.TestCase):
    def test_fixture_declares_explicit_initial_state_and_isolated_profile(self):
        spec = e2e_fixtures.load_fixture("smoke")
        profile = e2e_fixtures.fixture_profile(spec)

        self.assertEqual(spec.local_epubs, 3)
        self.assertEqual(spec.services, ("opds", "paris"))
        self.assertEqual(profile["name"], "e2e-smoke")
        self.assertFalse(profile["cifereca"]["copy_installed_plugins"])

    def test_prepare_resets_contamination_and_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {datasetctl.RUNTIME_ENV: tmp}):
            first = e2e_fixtures.prepare_fixture("smoke", start_services=False)
            digest = first["manifest"]["library_digest"]
            runtime = Path(first["manifest"]["runtime"])

            dirty = runtime / "downloads" / "leak.txt"
            dirty.parent.mkdir(parents=True, exist_ok=True)
            dirty.write_text("must disappear", encoding="utf-8")

            second = e2e_fixtures.prepare_fixture("smoke", start_services=False)

            self.assertEqual(second["manifest"]["library_digest"], digest)
            self.assertFalse(dirty.exists())
            self.assertTrue(Path(second["manifest"]["config"]).is_file())
            self.assertTrue(Path(second["manifest"]["plugins_config"]).is_file())
            self.assertEqual(second["env"]["CIFERECA_E2E_FIXTURE"], "smoke")
            for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
                self.assertTrue(Path(second["env"][key]).is_relative_to(Path(tmp)))

    def test_empty_fixture_has_no_user_state_or_external_services(self):
        spec = e2e_fixtures.load_fixture("empty")
        profile = e2e_fixtures.fixture_profile(spec)

        self.assertEqual(spec.local_epubs, 0)
        self.assertEqual(spec.services, ())
        self.assertEqual(datasetctl.enabled_service_configs(profile), {})

    def test_fixture_session_always_cleans_runtime(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {datasetctl.RUNTIME_ENV: tmp}):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                with e2e_fixtures.fixture_session("empty", start_services=False) as prepared:
                    runtime = Path(prepared["manifest"]["runtime"])
                    self.assertTrue((runtime / e2e_fixtures.MANIFEST_NAME).is_file())
                    raise RuntimeError("boom")

            self.assertFalse((runtime / e2e_fixtures.MANIFEST_NAME).exists())
            self.assertFalse((runtime / "library").exists())
            self.assertFalse((runtime / "xdg").exists())


if __name__ == "__main__":
    unittest.main()
