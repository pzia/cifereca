from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.package_partial_plugins import (
    PARTIAL_MANIFEST,
    build_partial_snapshot,
    verify_partial_snapshot,
)
from scripts.plugin_packaging import PackagingError, validate_dev_snapshot
from scripts.test_plugin_packaging import make_plugin


class PartialPluginReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plugins = self.root / "plugins"
        self.plugins.mkdir()
        make_plugin(self.plugins, "alpha", "org.cifereca.provider.alpha", "1.2.3")
        make_plugin(self.plugins, "beta", "org.cifereca.provider.beta", "2.0")

    def build(self, *packages: str) -> Path:
        return build_partial_snapshot(
            packages,
            plugins_dir=self.plugins,
            output_dir=self.root / "dist",
            snapshot_id="plugins-preview-test",
        )

    def test_one_package_contains_no_core_and_embedded_metadata(self) -> None:
        archive = self.build("org.cifereca.provider.alpha")
        dev = validate_dev_snapshot(archive)
        self.assertNotIn("core", dev)
        self.assertEqual(len(dev["plugins"]), 1)
        metadata = verify_partial_snapshot(archive)
        self.assertEqual(metadata["kind"], "cifereca-plugins-only-preview")
        self.assertEqual(metadata["id"], dev["id"])
        self.assertTrue(metadata["core_version_tested"])
        self.assertEqual(
            [row["package_id"] for row in metadata["packages"]],
            ["org.cifereca.provider.alpha"],
        )
        record = metadata["packages"][0]
        self.assertEqual(record["version"], "1.2.3")
        self.assertEqual(record["plugin_api"], ">=1.3,<2")
        self.assertEqual(record["plugins"], [{"id": "alpha.service", "version": "1.2.3"}])
        self.assertEqual(record["sha256"], dev["plugins"][0]["sha256"])
        with zipfile.ZipFile(archive) as z:
            self.assertIn("plugins-preview-test/" + PARTIAL_MANIFEST, z.namelist())
            self.assertEqual(
                hashlib.sha256(z.read("plugins-preview-test/" + record["path"])).hexdigest(),
                record["sha256"],
            )
        expected_checksum = f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
        self.assertEqual(
            archive.with_suffix(archive.suffix + ".sha256").read_text(encoding="utf-8"),
            expected_checksum,
        )

    def test_multiple_packages_have_sorted_complete_index(self) -> None:
        archive = self.build("org.cifereca.provider.beta", "org.cifereca.provider.alpha")
        metadata = verify_partial_snapshot(archive)
        self.assertEqual(
            [entry["package_id"] for entry in metadata["packages"]],
            ["org.cifereca.provider.alpha", "org.cifereca.provider.beta"],
        )
        self.assertEqual(len(validate_dev_snapshot(archive)["plugins"]), 2)

    def test_unknown_duplicate_and_empty_selection_rejected(self) -> None:
        for selected, expected in [
            ((), "au moins un"),
            (("org.cifereca.provider.alpha", "org.cifereca.provider.alpha"), "dupliqué"),
            (("org.cifereca.provider.unknown",), "inconnus"),
        ]:
            with self.subTest(selected=selected):
                with self.assertRaisesRegex(PackagingError, expected):
                    self.build(*selected)

    def test_target_core_and_api_preflight_accepts_exact_tested_environment(self) -> None:
        archive = self.build("org.cifereca.provider.alpha", "org.cifereca.provider.beta")
        metadata = verify_partial_snapshot(archive)
        accepted = verify_partial_snapshot(
            archive,
            target_core_version=metadata["core_version_tested"],
            target_plugin_api_version="1.4.0",
        )
        self.assertEqual(accepted, metadata)

    def test_target_core_drift_is_rejected_even_when_api_is_compatible(self) -> None:
        archive = self.build("org.cifereca.provider.alpha")
        with self.assertRaisesRegex(PackagingError, "Core cible"):
            verify_partial_snapshot(
                archive, target_core_version="0.0.0",
                target_plugin_api_version="1.4.0",
            )

    def test_target_api_incompatibility_is_rejected_before_install(self) -> None:
        archive = self.build("org.cifereca.provider.alpha")
        tested = verify_partial_snapshot(archive)["core_version_tested"]
        with self.assertRaisesRegex(PackagingError, "incompatible avec Plugin API"):
            verify_partial_snapshot(
                archive, target_core_version=tested,
                target_plugin_api_version="3.0.0",
            )
        with self.assertRaisesRegex(PackagingError, "Plugin API cible invalide"):
            verify_partial_snapshot(
                archive, target_core_version=tested,
                target_plugin_api_version="not-a-version",
            )

    def test_partial_target_specification_is_forbidden(self) -> None:
        archive = self.build("org.cifereca.provider.alpha")
        for kwargs in (
            {"target_core_version": "0.9.44+dev.11"},
            {"target_plugin_api_version": "1.4.0"},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(PackagingError, "requises ensemble"):
                    verify_partial_snapshot(archive, **kwargs)

    def test_modified_embedded_metadata_is_detected(self) -> None:
        original = self.build("org.cifereca.provider.alpha")
        corrupted = self.root / "tampered.zip"
        with zipfile.ZipFile(original) as input_zip, zipfile.ZipFile(corrupted, "w") as output_zip:
            for item in input_zip.infolist():
                payload = input_zip.read(item.filename)
                if item.filename.endswith("/" + PARTIAL_MANIFEST):
                    obj = json.loads(payload)
                    obj["packages"][0]["version"] = "99.0"
                    payload = json.dumps(obj).encode("utf-8")
                output_zip.writestr(item, payload)
        with self.assertRaisesRegex(PackagingError, "incohérent"):
            verify_partial_snapshot(corrupted)


if __name__ == "__main__":
    unittest.main()
