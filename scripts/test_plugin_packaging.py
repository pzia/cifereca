from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.plugin_packaging import (
    build_all_in_one,
    build_dev_snapshot,
    build_plugin_zip,
    discover_plugins,
    validate_dev_snapshot,
)


def make_plugin(root: Path, name: str, package_id: str, version: str) -> Path:
    directory = root / name
    directory.mkdir()
    manifest = {
        "manifest": 1,
        "package": {
            "id": package_id,
            "version": version,
            "name": name,
            "publisher": "tests",
            "plugin_api": ">=1.3,<2",
        },
        "plugins": [
            {
                "id": f"{name}.service",
                "type": "provider.library",
                "entrypoint": "plugin:PLUGIN",
                "version": version,
            }
        ],
    }
    (directory / "cifereca-plugin.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (directory / "plugin.py").write_text("PLUGIN = object()\n", encoding="utf-8")
    return directory


class PluginPackagingTests(unittest.TestCase):
    def test_plugin_zip_has_manifest_at_root_and_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = make_plugin(
                root, "alpha", "org.cifereca.provider.alpha", "1.2.3"
            )
            one = build_plugin_zip(plugin, root / "one")
            two = build_plugin_zip(plugin, root / "two")
            self.assertEqual(
                hashlib.sha256(one.read_bytes()).hexdigest(),
                hashlib.sha256(two.read_bytes()).hexdigest(),
            )
            with zipfile.ZipFile(one) as archive:
                self.assertIn("cifereca-plugin.json", archive.namelist())
                self.assertIn("plugin.py", archive.namelist())
                self.assertFalse(
                    any(name.startswith("alpha/") for name in archive.namelist())
                )

    def test_all_in_one_contains_individual_plugin_zips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugins = root / "plugins"
            plugins.mkdir()
            make_plugin(
                plugins, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            make_plugin(
                plugins, "beta", "org.cifereca.provider.beta", "2.0"
            )
            output = build_all_in_one(
                discover_plugins(plugins),
                root / "dist",
                filename="all.zip",
            )
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                bundle = json.loads(archive.read("cifereca-plugins.json"))
            self.assertEqual(len(bundle["plugins"]), 2)
            self.assertEqual(
                len(
                    [
                        name
                        for name in names
                        if name.startswith("packages/") and name.endswith(".zip")
                    ]
                ),
                2,
            )
            self.assertIn("SHA256SUMS", names)

    def test_snapshot_matches_core044_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugins = root / "plugins"
            plugins.mkdir()
            make_plugin(
                plugins, "alpha", "org.cifereca.provider.alpha", "1.0"
            )
            make_plugin(
                plugins, "beta", "org.cifereca.provider.beta", "2.0"
            )
            output = build_dev_snapshot(
                discover_plugins(plugins),
                root / "dist",
                snapshot_id="cifereca-dev-test",
            )
            manifest = validate_dev_snapshot(output)
            self.assertEqual(manifest["format"], 1)
            self.assertEqual(manifest["kind"], "cifereca-dev-snapshot")
            self.assertEqual(manifest["id"], "cifereca-dev-test")
            self.assertEqual(len(manifest["plugins"]), 2)
            self.assertTrue(all(row["activate"] for row in manifest["plugins"]))
            with zipfile.ZipFile(output) as archive:
                roots = {name.split("/", 1)[0] for name in archive.namelist()}
                self.assertEqual(roots, {"cifereca-dev-test"})
                self.assertIn(
                    "cifereca-dev-test/cifereca-dev.json",
                    archive.namelist(),
                )
                for row in manifest["plugins"]:
                    member = f"cifereca-dev-test/{row['path']}"
                    payload = archive.read(member)
                    self.assertEqual(
                        hashlib.sha256(payload).hexdigest(),
                        row["sha256"],
                    )

    def test_real_repository_has_13_packages(self) -> None:
        plugins = Path(__file__).resolve().parents[1] / "plugins"
        self.assertEqual(len(discover_plugins(plugins)), 13)


if __name__ == "__main__":
    unittest.main()
