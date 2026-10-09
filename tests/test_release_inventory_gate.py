"""Exercise the release-only gate on synthetic trees, never the live snapshot."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_release_inventory.py"
spec = importlib.util.spec_from_file_location("release_inventory", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class ReleaseInventoryGateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "VERSION").write_text("1.0\n", encoding="utf-8")
        self.manifest = self.root / "src/plugins/example/cifereca-plugin.json"
        self.snapshot = self.root / "src/cifereca/plugin-release.json"
        self.write(self.manifest, {"package": {"id": "example", "version": "2.0", "name": "Example"}})
        self.release = {"release": "1.0", "packages": {"example": {"version": "2.0", "name": "Example"}}}
        self.write(self.snapshot, self.release)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_matching_inventory_passes(self):
        gate.validate(self.root)

    def test_release_version_mismatch_fails(self):
        self.release["release"] = "0.9"
        self.write(self.snapshot, self.release)
        with self.assertRaises(ValueError):
            gate.validate(self.root)

    def test_changed_version_or_name_fails(self):
        for key in ("version", "name"):
            with self.subTest(key=key):
                row = {"id": "example", "version": "2.0", "name": "Example"}
                row[key] = "changed"
                self.write(self.manifest, {"package": row})
                with self.assertRaises(ValueError):
                    gate.validate(self.root)

    def test_missing_or_obsolete_package_fails(self):
        for packages in ({}, {**self.release["packages"], "obsolete": {"version": "1", "name": "Old"}}):
            with self.subTest(packages=packages):
                self.write(self.snapshot, {"release": "1.0", "packages": packages})
                with self.assertRaises(ValueError):
                    gate.validate(self.root)

    def test_duplicate_package_fails(self):
        self.write(self.root / "src/plugins/duplicate/cifereca-plugin.json", json.loads(self.manifest.read_text()))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            gate.validate(self.root)

    def test_cli_returns_failure_for_drift(self):
        self.release["packages"] = {}
        self.write(self.snapshot, self.release)
        result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Release inventory rejected", result.stderr)

    def test_ordinary_manifest_check_accepts_drift_but_release_rejects_it(self):
        ordinary_spec = importlib.util.spec_from_file_location(
            "ordinary_metadata", ROOT / "tests/test_core_538_release_metadata_consistency.py"
        )
        ordinary = importlib.util.module_from_spec(ordinary_spec)
        ordinary_spec.loader.exec_module(ordinary)
        ordinary.ROOT = self.root
        self.release["packages"]["example"]["version"] = "old"
        self.write(self.snapshot, self.release)
        ordinary.test_source_plugin_manifests_have_unique_ids_and_metadata()
        with self.assertRaises(ValueError):
            gate.validate(self.root)

    def test_publication_runs_gate_before_build_and_upload(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        check = workflow.index("python scripts/check_release_inventory.py")
        self.assertLess(check, workflow.index("- name: Build Core wheel"))
        self.assertLess(check, workflow.index("- name: Create or update draft release"))


if __name__ == "__main__":
    unittest.main()
