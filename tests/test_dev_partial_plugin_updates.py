"""Plugin-only snapshot upgrade/rollback regressions for CI/CD #577.

The real ZIP packager, ZIP verifier, extractor and backup/restore code run;
only the external CLI process and installed runtime symlink are stubbed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cifereca import dev_updater as dev
from scripts.package_partial_plugins import build_partial_snapshot, verify_partial_snapshot
from scripts.test_plugin_packaging import make_plugin


ALPHA = "org.cifereca.provider.alpha"
BETA = "org.cifereca.provider.beta"
UNTOUCHED = "org.cifereca.provider.untouched"


class PartialUpgradeTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plugins = self.root / "source-plugins"
        self.plugins.mkdir()
        make_plugin(self.plugins, "alpha", ALPHA, "1.2.3")
        make_plugin(self.plugins, "beta", BETA, "2.0")
        self.store = self.root / "installed-plugins"
        self.store.mkdir()
        self.state = self.root / "state"
        self.core = self.root / "installed-core"
        self.core.mkdir()
        self._initial(ALPHA, version="0.9", active=False)
        self._initial(UNTOUCHED, version="4.2", active=True)
        self.snapshot = build_partial_snapshot(
            [BETA, ALPHA], plugins_dir=self.plugins,
            output_dir=self.root / "dist",
            snapshot_id="plugins-only-transaction-test",
        )
        self._target_runtime(verify_partial_snapshot(self.snapshot)["core_version_tested"])

    def _target_runtime(self, version: str, api: str = "1.4.0") -> None:
        # Real files are parsed by the preflight, not mocks.
        release = self.core / "current"
        release.mkdir(parents=True, exist_ok=True)
        (release / "VERSION").write_text(version + "\n", encoding="utf-8")
        (release / "pyproject.toml").write_text(
            '[project]\nname="cifereca"\nversion="' + version +
            '"\n[project.scripts]\ncifereca="x:main"\ncifereca-cli="x:main"\n'
            'cifereca-update="x:main"\n', encoding="utf-8",
        )
        api_file = release / "src" / "cifereca" / "plugin_api" / "version.py"
        api_file.parent.mkdir(parents=True, exist_ok=True)
        api_file.write_text(f'PLUGIN_API_VERSION = "{api}"\n', encoding="utf-8")

    def _edited_snapshot(self, *, index_edit=None, dev_edit=None) -> Path:
        output = self.root / ("changed-" + str(len(list(self.root.glob("changed-*.zip")))) + ".zip")
        with zipfile.ZipFile(self.snapshot) as original, zipfile.ZipFile(output, "w") as rewritten:
            for info in original.infolist():
                payload = original.read(info.filename)
                if info.filename.endswith("/cifereca-plugins-only.json") and index_edit is not None:
                    raw = json.loads(payload)
                    index_edit(raw)
                    payload = json.dumps(raw).encode("utf-8")
                elif info.filename.endswith("/cifereca-dev.json") and dev_edit is not None:
                    raw = json.loads(payload)
                    dev_edit(raw)
                    payload = json.dumps(raw).encode("utf-8")
                rewritten.writestr(info, payload)
        return output

    def _assert_refused_before_mutation(self, archive: Path, reason: str) -> None:
        patches = self._sandbox()
        with (patches[0], patches[1], patches[2],
              patches[3] as core_install,
              patch.object(dev, "_backup_plugins") as backup,
              patches[4] as smoke, patches[5] as plugin_install):
            with self.assertRaisesRegex(dev.DevUpdateError, reason):
                dev.apply_snapshot(archive)
        backup.assert_not_called()
        core_install.assert_not_called()
        plugin_install.assert_not_called()
        smoke.assert_not_called()
        self.assertFalse(self.state.exists())
        self.assertEqual(self._version(ALPHA), "0.9")
        self.assertIsNone(self._version(BETA))

    def _initial(self, package_id: str, *, version: str, active: bool) -> None:
        folder = self.store / package_id
        folder.mkdir(parents=True)
        (folder / "version.txt").write_text(version, encoding="utf-8")
        (folder / "current.json").write_text(json.dumps({"active": active}), encoding="utf-8")

    def _version(self, package_id: str) -> str | None:
        path = self.store / package_id / "version.txt"
        return path.read_text(encoding="utf-8") if path.exists() else None

    def _active(self, package_id: str) -> bool:
        return json.loads((self.store / package_id / "current.json").read_text(encoding="utf-8"))["active"]

    def _fake_install(self, archive: Path, *, activate: bool) -> None:
        with zipfile.ZipFile(archive) as z:
            package = json.loads(z.read("cifereca-plugin.json"))["package"]
        folder = self.store / package["id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "version.txt").write_text(str(package["version"]), encoding="utf-8")
        (folder / "current.json").write_text(json.dumps({"active": activate}), encoding="utf-8")

    def _sandbox(self):
        """Patch only the external process and absolute paths, not the rollback."""
        return (
            patch.object(dev, "_plugin_store_root", return_value=self.store),
            patch.object(dev, "state_dir", return_value=self.state),
            patch.object(dev.updater, "install_root", return_value=self.core),
            patch.object(dev.updater, "install_archive"),
            patch.object(dev, "_smoke_plugins", return_value={"plugin_count": 2}),
            patch.object(dev, "_install_plugin_replace", side_effect=self._fake_install),
        )

    def test_partial_upgrade_keeps_core_and_unselected_plugin_unchanged(self) -> None:
        verify_partial_snapshot(self.snapshot)
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3] as core_install, patches[4] as smoke, patches[5] as plugin_install:
            result = dev.apply_snapshot(self.snapshot)

        self.assertTrue(result["ok"])
        self.assertFalse(result["core_changed"])
        self.assertIsNone(result["core"])
        self.assertEqual(result["plugins"], [ALPHA, BETA])
        core_install.assert_not_called()
        self.assertEqual(plugin_install.call_count, 2)
        smoke.assert_called_once_with([ALPHA, BETA])
        self.assertEqual(self._version(ALPHA), "1.2.3")
        self.assertFalse(self._active(ALPHA))  # Existing disabled state survives.
        self.assertEqual(self._version(BETA), "2.0")
        self.assertTrue(self._active(BETA))  # New package defaults to active.
        self.assertEqual(self._version(UNTOUCHED), "4.2")
        self.assertTrue(self._active(UNTOUCHED))
        # Backups are kept for a later operator-directed recovery.
        backup = Path(result["backup"]) / "plugins" / ALPHA
        self.assertEqual((backup / "version.txt").read_text(encoding="utf-8"), "0.9")

    def test_failed_second_install_restores_every_selected_package(self) -> None:
        def fail_second(archive: Path, *, activate: bool) -> None:
            self._fake_install(archive, activate=activate)
            with zipfile.ZipFile(archive) as z:
                package_id = json.loads(z.read("cifereca-plugin.json"))["package"]["id"]
            if package_id == BETA:
                raise dev.DevUpdateError("simulated second-plugin failure")

        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3] as core_install, patches[4], patch.object(dev, "_install_plugin_replace", side_effect=fail_second):
            with self.assertRaisesRegex(dev.DevUpdateError, "simulated second-plugin failure"):
                dev.apply_snapshot(self.snapshot)

        core_install.assert_not_called()
        self.assertEqual(self._version(ALPHA), "0.9")
        self.assertFalse(self._active(ALPHA))
        self.assertIsNone(self._version(BETA))
        self.assertEqual(self._version(UNTOUCHED), "4.2")

    def test_smoke_failure_rolls_back_both_packages(self) -> None:
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3] as core_install, patch.object(dev, "_smoke_plugins", side_effect=dev.DevUpdateError("simulated incompatible plugin")), patches[5]:
            with self.assertRaisesRegex(dev.DevUpdateError, "simulated incompatible plugin"):
                dev.apply_snapshot(self.snapshot)

        core_install.assert_not_called()
        self.assertEqual(self._version(ALPHA), "0.9")
        self.assertFalse(self._active(ALPHA))
        self.assertIsNone(self._version(BETA))
        self.assertEqual(self._version(UNTOUCHED), "4.2")

    def test_dry_run_leaves_store_and_backup_untouched(self) -> None:
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3] as core_install, patch.object(dev, "_backup_plugins") as backup, patches[4] as smoke, patches[5] as install:
            result = dev.apply_snapshot(self.snapshot, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["core_changed"])
        self.assertEqual(result["plugins"], [ALPHA, BETA])
        backup.assert_not_called()
        core_install.assert_not_called()
        install.assert_not_called()
        smoke.assert_not_called()
        self.assertFalse(self.state.exists())
        self.assertEqual(self._version(ALPHA), "0.9")
        self.assertIsNone(self._version(BETA))

    def test_corrupted_package_hash_aborts_before_install_or_backup(self) -> None:
        corrupt = self.root / "mismatched.zip"
        with zipfile.ZipFile(self.snapshot) as original, zipfile.ZipFile(corrupt, "w") as output:
            for info in original.infolist():
                data = original.read(info.filename)
                if info.filename.endswith("/cifereca-dev.json"):
                    manifest = json.loads(data)
                    manifest["plugins"][0]["sha256"] = "0" * 64
                    data = json.dumps(manifest).encode("utf-8")
                output.writestr(info, data)
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3] as core_install, patch.object(dev, "_backup_plugins") as backup, patches[4] as smoke, patches[5] as install:
            with self.assertRaisesRegex(dev.DevUpdateError, "SHA-256"):
                dev.apply_snapshot(corrupt)
        core_install.assert_not_called()
        backup.assert_not_called()
        install.assert_not_called()
        smoke.assert_not_called()
        self.assertEqual(self._version(ALPHA), "0.9")


    def test_indexed_partial_requires_exact_installed_core_before_mutation(self) -> None:
        self._target_runtime("0.0.1")
        self._assert_refused_before_mutation(self.snapshot, "Core cible.*différent du Core testé")
        self._target_runtime(verify_partial_snapshot(self.snapshot)["core_version_tested"])
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            self.assertTrue(dev.apply_snapshot(self.snapshot, dry_run=True)["plugins_only_indexed"])

    def test_indexed_partial_rejects_incompatible_target_plugin_api(self) -> None:
        version = verify_partial_snapshot(self.snapshot)["core_version_tested"]
        self._target_runtime(version, api="3.0.0")
        self._assert_refused_before_mutation(self.snapshot, "incompatible avec Plugin API")
        self._target_runtime(version, api="1.4.0")

    def test_missing_target_core_metadata_fails_before_mutation(self) -> None:
        (self.core / "current" / "pyproject.toml").unlink()
        self._assert_refused_before_mutation(self.snapshot, "runtime Core cible")

    def test_incoherent_index_is_rejected_before_any_install(self) -> None:
        cases = (
            (lambda index: index.update(id="different-id"), "provenance"),
            (lambda index: index.update(source_git_commit=""), "provenance"),
            (lambda index: index["packages"][0].update(sha256="0" * 64), "incohérent"),
            (lambda index: index["packages"][0].update(version="99.0"), "incohérent"),
            (lambda index: index["packages"].pop(), "incohérent"),
        )
        for edit, message in cases:
            with self.subTest(message=message, edit=edit):
                self._assert_refused_before_mutation(self._edited_snapshot(index_edit=edit), message)

    def test_plugins_only_index_rejects_smuggled_core(self) -> None:
        def smuggle(dev_manifest):
            dev_manifest["core"] = dev_manifest["plugins"][0]["path"]

        self._assert_refused_before_mutation(
            self._edited_snapshot(dev_edit=smuggle), "Core interdit",
        )

    def test_legacy_dev_snapshot_without_index_keeps_old_behavior(self) -> None:
        from scripts.plugin_packaging import build_dev_snapshot
        legacy = build_dev_snapshot(
            [self.plugins / "alpha"], output_dir=self.root / "dist",
            snapshot_id="historical-generic-dev",
        )
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3] as core_install, patches[4], patches[5] as install:
            result = dev.apply_snapshot(legacy)
        self.assertFalse(result["plugins_only_indexed"])
        self.assertEqual(result["plugins"], [ALPHA])
        self.assertEqual(self._version(ALPHA), "1.2.3")
        core_install.assert_not_called()
        install.assert_called_once()

    def test_indexed_preview_supports_one_package_and_isolates_others(self) -> None:
        single = build_partial_snapshot(
            [ALPHA], plugins_dir=self.plugins, output_dir=self.root / "dist",
            snapshot_id="single-plugin-compat",
        )
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3] as core_install, patches[4], patches[5] as plugin_install:
            result = dev.apply_snapshot(single)
        self.assertTrue(result["plugins_only_indexed"])
        self.assertEqual(result["plugins"], [ALPHA])
        self.assertIsNone(self._version(BETA))
        self.assertEqual(self._version(UNTOUCHED), "4.2")
        core_install.assert_not_called()
        plugin_install.assert_called_once()


if __name__ == "__main__":
    unittest.main()
