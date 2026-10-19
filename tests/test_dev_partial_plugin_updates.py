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
from cifereca.plugin_release import installed_plugin_update_receipt
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

    def _without_partial_index(self, *, strip_scope: bool = False) -> Path:
        output = self.root / (
            "stripped-" + str(len(list(self.root.glob("stripped-*.zip")))) + ".zip"
        )
        with zipfile.ZipFile(self.snapshot) as source, zipfile.ZipFile(output, "w") as target:
            for info in source.infolist():
                if info.filename.endswith("/cifereca-plugins-only.json"):
                    continue
                payload = source.read(info.filename)
                if strip_scope and info.filename.endswith("/cifereca-dev.json"):
                    manifest = json.loads(payload)
                    manifest.pop("scope", None)
                    payload = json.dumps(manifest).encode("utf-8")
                target.writestr(info, payload)
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

    def _receipt(self, package: str):
        target = self.store / package / ".cifereca-last-plugin-update.json"
        return json.loads(target.read_text(encoding="utf-8")) if target.exists() else None

    def test_receipt_only_after_success_is_public_and_tracks_exact_package(self) -> None:
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = dev.apply_snapshot(self.snapshot)
        for package, version in ((ALPHA, "1.2.3"), (BETA, "2.0")):
            receipt = self._receipt(package)
            self.assertEqual(receipt["schema"], 1)
            self.assertEqual(receipt["scope"], "plugins-only")
            self.assertEqual(receipt["package_id"], package)
            self.assertEqual(receipt["version"], version)
            self.assertEqual(receipt["snapshot_sha256"], result["sha256"])
            self.assertRegex(receipt["package_sha256"], r"^[a-f0-9]{64}$")
            self.assertFalse(receipt["trusted_digest_verified"])
            self.assertIs(receipt["signature_verified"], False)
            self.assertNotIn(str(self.root), json.dumps(receipt))
            self.assertIsNotNone(installed_plugin_update_receipt(
                self.store / package, package, [version],
            ))
        self.assertIsNone(self._receipt(UNTOUCHED))

    def test_verified_digest_is_recorded_without_fabricating_signature(self) -> None:
        digest = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch.object(dev, "_verify_strict_installed_packages"):
            dev.apply_snapshot(
                self.snapshot, expected_sha256=digest, require_plugins_only=True,
            )
        self.assertTrue(self._receipt(ALPHA)["trusted_digest_verified"])
        self.assertFalse(self._receipt(ALPHA)["signature_verified"])

    def test_smoke_error_and_failed_receipt_restore_preupdate_state(self) -> None:
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3], \
             patch.object(dev, "_smoke_plugins", side_effect=dev.DevUpdateError("test-smoke")), patches[5]:
            with self.assertRaisesRegex(dev.DevUpdateError, "test-smoke"):
                dev.apply_snapshot(self.snapshot)
        self.assertIsNone(self._receipt(ALPHA))
        self.assertIsNone(self._receipt(BETA))
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch.object(dev, "_record_partial_install_provenance", side_effect=OSError("test-file-io")):
            with self.assertRaisesRegex(OSError, "test-file-io"):
                dev.apply_snapshot(self.snapshot)
        self.assertEqual(self._version(ALPHA), "0.9")
        self.assertIsNone(self._version(BETA))
        self.assertIsNone(self._receipt(ALPHA))

    def test_late_partial_receipt_failure_rolls_back_first_written_receipt(self) -> None:
        write_original = dev._record_partial_install_provenance

        def partially_write(indexed, packages, **kwargs):
            write_original(indexed, packages[:1], **kwargs)
            assert self._receipt(ALPHA) is not None
            raise OSError("failure writing second receipt")

        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch.object(dev, "_record_partial_install_provenance", side_effect=partially_write):
            with self.assertRaisesRegex(OSError, "second receipt"):
                dev.apply_snapshot(self.snapshot)
        self.assertEqual(self._version(ALPHA), "0.9")
        self.assertIsNone(self._version(BETA))
        self.assertIsNone(self._receipt(ALPHA))
        self.assertIsNone(self._receipt(BETA))

    def test_untrusted_or_stale_receipt_is_ignored(self) -> None:
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            dev.apply_snapshot(self.snapshot)
        root = self.store / ALPHA
        receipt = root / ".cifereca-last-plugin-update.json"
        good = receipt.read_text(encoding="utf-8")
        self.assertIsNone(installed_plugin_update_receipt(root, ALPHA, ["0.9"]))
        receipt.write_text(good.replace('"signature_verified": false', '"signature_verified": true'))
        self.assertIsNone(installed_plugin_update_receipt(root, ALPHA, ["1.2.3"]))
        receipt.write_text(good)
        other = self.root / "outside"
        other.write_text(good)
        receipt.unlink()
        receipt.symlink_to(other)
        self.assertIsNone(installed_plugin_update_receipt(root, ALPHA, ["1.2.3"]))
        self.assertEqual(other.read_text(), good)

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

    def test_partial_marker_requires_index_before_mutation(self) -> None:
        with zipfile.ZipFile(self.snapshot) as archive:
            root = verify_partial_snapshot(self.snapshot)["id"]
            manifest = json.loads(archive.read(f"{root}/cifereca-dev.json"))
        self.assertEqual(manifest["scope"], "plugins-only")
        stripped = self._without_partial_index()
        self._assert_refused_before_mutation(stripped, "plugins-only.*sans index")
        # Dry-run must use the exact same safety preflight.
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3] as core_install, patches[4], patches[5]:
            with self.assertRaisesRegex(dev.DevUpdateError, "plugins-only.*sans index"):
                dev.apply_snapshot(stripped, dry_run=True)
        core_install.assert_not_called()

    def test_unknown_scope_rejected_without_backup(self) -> None:
        def invalid_scope(manifest):
            manifest["scope"] = "unexpected-runtime-mode"
        self._assert_refused_before_mutation(
            self._edited_snapshot(dev_edit=invalid_scope), "portée.*invalide",
        )

    def test_packager_rejects_partial_index_when_scope_missing(self) -> None:
        from scripts.plugin_packaging import PackagingError
        def erase_scope(manifest):
            manifest.pop("scope", None)
        with self.assertRaisesRegex(PackagingError, "marqueur de portée"):
            verify_partial_snapshot(self._edited_snapshot(dev_edit=erase_scope))

    def test_legacy_generic_snapshot_has_no_marker_and_no_index(self) -> None:
        from scripts.plugin_packaging import build_dev_snapshot
        generic = build_dev_snapshot(
            [self.plugins / "alpha"], output_dir=self.root / "dist",
            snapshot_id="old-generic-without-marker",
        )
        with zipfile.ZipFile(generic) as archive:
            manifest = json.loads(archive.read("old-generic-without-marker/cifereca-dev.json"))
            self.assertNotIn("scope", manifest)
            self.assertNotIn(
                "old-generic-without-marker/cifereca-plugins-only.json", archive.namelist(),
            )
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            self.assertFalse(dev.apply_snapshot(generic, dry_run=True)["plugins_only_indexed"])

    def test_indexed_legacy_partial_without_marker_still_checked(self) -> None:
        # Historical preview archives may include a provenance index but not
        # the new explicit marker. They must still enforce Core/API matching.
        def erase_scope(manifest):
            manifest.pop("scope", None)
        old_indexed = self._edited_snapshot(dev_edit=erase_scope)
        self._target_runtime("0.0.1")
        self._assert_refused_before_mutation(
            old_indexed, "Core cible.*différent du Core testé",
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


    def test_trusted_zip_digest_accepted_before_dry_run_and_real_install(self) -> None:
        digest = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = dev.apply_snapshot(
                self.snapshot, dry_run=True, expected_sha256=digest.upper(),
            )
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["sha256"], digest)
        self.assertEqual(self._version(ALPHA), "0.9")
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            applied = dev.apply_snapshot(self.snapshot, expected_sha256=digest)
        self.assertTrue(applied["plugins_only_indexed"])
        self.assertEqual(self._version(ALPHA), "1.2.3")

    def test_changed_zip_is_rejected_before_extraction_or_backup(self) -> None:
        trusted = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()
        changed = self._edited_snapshot(dev_edit=lambda data: data.update(created_at="changed"))
        self.assertNotEqual(hashlib.sha256(changed.read_bytes()).hexdigest(), trusted)
        with patch.object(dev, "_extract_snapshot") as extract, \
             patch.object(dev, "_backup_plugins") as backup, \
             patch.object(dev, "_install_plugin_replace") as install:
            with self.assertRaisesRegex(dev.DevUpdateError, "SHA-256 du snapshot"):
                dev.apply_snapshot(changed, expected_sha256=trusted)
        extract.assert_not_called()
        backup.assert_not_called()
        install.assert_not_called()
        self.assertEqual(self._version(ALPHA), "0.9")
        self.assertFalse(self.state.exists())

    def test_invalid_trusted_digest_rejected_before_extraction_even_in_dry_run(self) -> None:
        for digest in ("", "0" * 63, "0" * 65, "gg" * 32, "0" * 64 + chr(10)):
            with self.subTest(digest=digest), patch.object(dev, "_extract_snapshot") as extract:
                with self.assertRaisesRegex(dev.DevUpdateError, "empreinte SHA-256 attendue invalide"):
                    dev.apply_snapshot(self.snapshot, dry_run=True, expected_sha256=digest)
                extract.assert_not_called()
        self.assertEqual(self._version(ALPHA), "0.9")

    def test_cli_expected_sha256_is_checked_before_extracting(self) -> None:
        with patch.object(dev, "_extract_snapshot") as extract, \
             patch.object(dev, "_print") as display:
            code = dev.main([str(self.snapshot), "--dry-run", "--expected-sha256", "0" * 64])
        self.assertEqual(code, 1)
        extract.assert_not_called()
        self.assertIn("SHA-256 du snapshot", str(display.call_args))




    def test_strict_mode_refuses_missing_trusted_digest_before_extraction(self) -> None:
        with patch.object(dev, "_extract_snapshot") as extract, \
             patch.object(dev, "_backup_plugins") as backup, \
             patch.object(dev, "_install_plugin_replace") as install:
            with self.assertRaisesRegex(dev.DevUpdateError, "expected-sha256"):
                dev.apply_snapshot(self.snapshot, require_plugins_only=True)
        extract.assert_not_called()
        backup.assert_not_called()
        install.assert_not_called()
        self.assertEqual(self._version(ALPHA), "0.9")
        self.assertFalse(self.state.exists())

    def test_strict_mode_accepts_verified_selected_packages_and_keeps_disabled_state(self) -> None:
        digest = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3] as core_install, patches[4] as smoke, patches[5] as install, \
             patch.object(dev, "_verify_strict_installed_packages") as verify:
            dry = dev.apply_snapshot(
                self.snapshot, dry_run=True, expected_sha256=digest,
                require_plugins_only=True,
            )
            assert dry["plugins_only_indexed"] and dry["strict_plugins_only"]
            assert dry["trusted_digest_verified"]
            assert dry["core_changed"] is False
            applied = dev.apply_snapshot(
                self.snapshot, expected_sha256=digest,
                require_plugins_only=True,
            )
        self.assertEqual(applied["plugins"], [ALPHA, BETA])
        self.assertFalse(applied["core_changed"])
        self.assertEqual(self._version(ALPHA), "1.2.3")
        self.assertFalse(self._active(ALPHA))
        self.assertEqual(self._version(BETA), "2.0")
        self.assertEqual(self._version(UNTOUCHED), "4.2")
        core_install.assert_not_called()
        self.assertEqual(install.call_count, 2)
        smoke.assert_called_once_with([ALPHA, BETA])
        verify.assert_called_once()
        self.assertEqual(
            [(package, version, active) for package, version, _path, active in verify.call_args.args[0]],
            [(ALPHA, "1.2.3", False), (BETA, "2.0", True)],
        )

    def test_strict_mode_cannot_downgrade_to_unmarked_legacy_snapshot(self) -> None:
        from scripts.plugin_packaging import build_dev_snapshot
        historical = build_dev_snapshot(
            [self.plugins / "alpha"], output_dir=self.root / "dist",
            snapshot_id="generic-legacy-for-strict",
        )
        # Even a digest matching the supplied ZIP does not turn a generic
        # snapshot into an explicitly indexed plugins-only release.
        digest = hashlib.sha256(historical.read_bytes()).hexdigest()
        self._assert_strict_refusal(historical, digest, "marqueur de portée et index")

        def erase_scope(manifest):
            manifest.pop("scope", None)

        unmarked = self._edited_snapshot(dev_edit=erase_scope)
        digest = hashlib.sha256(unmarked.read_bytes()).hexdigest()
        self._assert_strict_refusal(unmarked, digest, "marqueur de portée et index")

    def test_strict_mode_refuses_stripped_index_even_with_matching_zip_digest(self) -> None:
        stripped = self._without_partial_index()
        digest = hashlib.sha256(stripped.read_bytes()).hexdigest()
        self._assert_strict_refusal(stripped, digest, "plugins-only.*sans index")

    def _assert_strict_refusal(self, archive: Path, digest: str, message: str) -> None:
        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3] as core, \
             patch.object(dev, "_backup_plugins") as backup, \
             patches[4] as smoke, patches[5] as install:
            with self.assertRaisesRegex(dev.DevUpdateError, message):
                dev.apply_snapshot(
                    archive, expected_sha256=digest, require_plugins_only=True,
                )
        core.assert_not_called()
        backup.assert_not_called()
        smoke.assert_not_called()
        install.assert_not_called()
        self.assertEqual(self._version(ALPHA), "0.9")
        self.assertFalse(self.state.exists())

    def test_trusted_zip_is_extracted_from_private_verified_copy(self) -> None:
        digest = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()
        original_extract = dev._extract_snapshot
        extracted_names = []

        def race_original_archive(archive: Path, destination: Path):
            extracted_names.append(archive.name)
            self.assertNotEqual(archive, self.snapshot)
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), digest)
            # Simulate a source archive replaced just after the checksum and
            # before extraction. The staged copy must remain unaffected.
            self.snapshot.write_bytes(b"replaced by a different archive")
            return original_extract(archive, destination)

        patches = self._sandbox()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch.object(dev, "_extract_snapshot", side_effect=race_original_archive):
            dry = dev.apply_snapshot(
                self.snapshot, expected_sha256=digest,
                require_plugins_only=True, dry_run=True,
            )
        self.assertEqual(extracted_names, ["verified-snapshot.zip"])
        self.assertTrue(dry["plugins_only_indexed"])
        self.assertEqual(dry["sha256"], digest)
        self.assertEqual(self._version(ALPHA), "0.9")

    def test_strict_cli_requires_digest_even_for_dry_run(self) -> None:
        with patch.object(dev, "_extract_snapshot") as extract, \
             patch.object(dev, "_print") as display:
            code = dev.main([str(self.snapshot), "--require-plugins-only", "--dry-run", "--json"])
        self.assertEqual(code, 1)
        extract.assert_not_called()
        self.assertIn("expected-sha256", str(display.call_args))

if __name__ == "__main__":
    unittest.main()
